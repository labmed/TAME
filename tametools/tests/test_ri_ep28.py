"""Independent formulas, published example, failure boundaries and plugin behavior."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np
import pandas as pd
from scipy import stats

from tametools import ri_ep28 as e
from tametools.models import ColumnSpec,TameDataset
from tametools.plugin_base import run_plugin
from tametools.io import read_tame


APPENDIX_B=np.array([8.9,9.2,9.4,9.4,9.5,9.5,9.5,9.6,9.6,9.6,9.6,9.7,9.7,9.7,9.7,9.7,9.8,9.9,9.9,10.2])


def dataset(values,sex=False):
    values=list(values);n=len(values)
    frame=pd.DataFrame({'person':[str(i) for i in range(n)],'measurement':[str(v) for v in values]})
    columns=[ColumnSpec('person','person',['ID']),ColumnSpec('measurement','measurement',['RESULT','NUM'])]
    meta={'COLUMN':{'person':{'ID':'subject'},'measurement':{'ID':'calcium','UNIT':'mg/dL'}}}
    if sex:
        frame['sex']=['A' if i<n//2 else 'B' for i in range(n)]
        columns.append(ColumnSpec('sex','sex',['SEX','CATEGORY']))
        meta['COLUMN']['sex']={'ID':'sex'}
    return TameDataset(frame,columns,meta)


def run(ds,**options):
    return run_plugin(ds,'RI_EP28',ds.meta,'study',{'SUBJECT_ID':'subject','BOOTSTRAP_N':120,**options})


class NumericalTests(unittest.TestCase):
    def test_appendix_b_published_calcium_example(self):
        result=e.robust_estimate(APPENDIX_B)
        self.assertAlmostEqual(result.details['location'],9.6244,delta=.0001)
        self.assertAlmostEqual(result.details['scale'],.27043,delta=.00001)
        # The supplied appendix prints rounded intermediate constants. Record the
        # full-precision discrepancy in validation; its final two-decimal RI agrees.
        self.assertAlmostEqual(result.details['location_se'],.04816,delta=.00005)
        self.assertEqual(round(result.low,2),9.05)
        self.assertEqual(round(result.high,2),10.20)

    def test_nonparametric_r_type6_and_type7(self):
        x=np.arange(1.,121.)
        self.assertAlmostEqual(e.estimate_limits(x).low,3.025)
        self.assertAlmostEqual(e.estimate_limits(x).high,117.975)
        self.assertAlmostEqual(e.estimate_limits(x,quantile_type=7).low,3.975)

    def test_parametric_formula_and_no_artificial_zero_clamp(self):
        x=np.arange(1.,121.)
        result=e.estimate_limits(x,'PARAMETRIC')
        self.assertAlmostEqual(result.low,x.mean()-stats.norm.ppf(.975)*x.std(ddof=1))
        self.assertLess(result.low,0)

    def test_log_reference_and_ci_backtransform(self):
        y=np.linspace(1.,3.,120);x=np.exp(y)
        a=e.estimate_limits(y,'PARAMETRIC');b=e.estimate_limits(x,'LOG_PARAMETRIC')
        np.testing.assert_allclose([b.low,b.high],np.exp([a.low,a.high]),rtol=1e-12)
        ac=e.reference_ci(y,'PARAMETRIC');bc=e.reference_ci(x,'LOG_PARAMETRIC')
        np.testing.assert_allclose(bc['bounds'],np.exp(ac['bounds']),rtol=1e-12)

    def test_log_robust_matches_robust_on_log_scale(self):
        x=np.exp(np.linspace(1.,3.,80))
        a=e.estimate_limits(np.log(x),'ROBUST');b=e.estimate_limits(x,'LOG_ROBUST')
        np.testing.assert_allclose([b.low,b.high],np.exp([a.low,a.high]),rtol=1e-12)

    def test_robust_is_invariant_to_affine_units(self):
        x=np.random.default_rng(31).normal(10,2,120)
        a=e.robust_estimate(x)
        for scale,shift in [(1e-5,2.),(1e4,-500.)]:
            b=e.robust_estimate(scale*x+shift)
            np.testing.assert_allclose([(b.low-shift)/scale,(b.high-shift)/scale],[a.low,a.high],rtol=1e-9)

    def test_rank_ci_120_and_unavailable_rank_not_clamped(self):
        ci=e.rank_ci(np.arange(1.,121.))
        self.assertEqual(ci['bounds'],[[1.,7.],[114.,120.]])
        self.assertGreaterEqual(min(ci['attained_confidence']),.90)
        small=e.rank_ci(np.arange(1.,21.))
        self.assertIsNone(small['bounds'][0][0])
        self.assertIsNone(small['bounds'][1][1])

    def test_rank_ci_respects_requested_coverage_and_confidence(self):
        a=e.rank_ci(np.arange(1.,1001.),coverage=.90,confidence=.95)
        b=e.rank_ci(np.arange(1.,1001.))
        self.assertNotEqual(a['bounds'],b['bounds'])
        self.assertGreaterEqual(min(a['attained_confidence']),.95)

    def test_bootstrap_common_indices_and_basic_identity(self):
        x=np.arange(1.,81.);ix=np.random.default_rng(3).integers(0,80,(200,80))
        a=e.reference_ci(x,'NONPARAMETRIC',ci_method='BOOTSTRAP',indices=ix)
        b=e.reference_ci(x,'NONPARAMETRIC',ci_method='BOOTSTRAP',indices=ix,bootstrap_type='BASIC')
        point=e.estimate_limits(x)
        np.testing.assert_allclose(b['bounds'],2*np.array([point.low,point.high])[:,None]-np.asarray(a['bounds'])[:,::-1])
        self.assertEqual(a['bootstrap_valid'],200)

    def test_invalid_bootstrap_indices_and_degenerate_robust(self):
        with self.assertRaises(ValueError):e.bootstrap_limits(np.arange(20.),'ROBUST',indices=np.ones((2,20))*.5)
        with self.assertRaises(ValueError):e.robust_estimate(np.ones(20))
        with self.assertRaises(ValueError):e.estimate_limits([0.,1.,2.],'LOG_PARAMETRIC')
        with self.assertRaises(ValueError):e.estimate_limits([1.,np.inf,2.])

    def test_tukey_strict_fence_reed_inclusive_threshold(self):
        self.assertEqual(e.detect_outliers([0,1,2,3],'REED').mask.tolist(),[True,False,False,True])
        x=np.array([0.,1.,2.,3.,4.,5.,6.,7.,8.])
        self.assertFalse(e.detect_outliers(x,'TUKEY').mask.any())

    def test_reed_block_unmasks_two_extremes(self):
        x=np.array([1,2,3,4,5,100,101.])
        self.assertFalse(e.detect_outliers(x,'REED').mask.any())
        self.assertEqual(np.flatnonzero(e.detect_outliers(x,'REED',reed_block_max=2).mask).tolist(),[5,6])

    def test_dixon_range_and_zero_iqr_safeguards(self):
        with self.assertRaises(ValueError):e.detect_outliers(np.arange(31.),'DIXON_Q')
        self.assertEqual(e.detect_outliers([1,2,3,4,100.],'DIXON_Q').mask.tolist(),[False]*4+[True])
        tied=e.detect_outliers([1]*10+[100],'TUKEY')
        self.assertFalse(tied.mask.any())
        self.assertTrue(tied.warnings)

    def test_horn_requires_positive_values(self):
        with self.assertRaises(ValueError):e.detect_outliers([-1,2,3,4],'HORN')

    def test_harris_boyd_against_scalar_formula(self):
        a=np.linspace(8,10,120);b=np.linspace(9,11,120)
        result=e.harris_boyd(a,b)
        expected=abs(a.mean()-b.mean())/np.sqrt(a.var(ddof=1)/120+b.var(ddof=1)/120)
        self.assertAlmostEqual(result['z'],expected)
        self.assertEqual(result['z_critical'],3)
        self.assertTrue(result['statistical_signal'])

    def test_verification_first_and_second_batches(self):
        for n,decision in [(2,'verification_criterion_met'),(3,'collect_second_batch_of_20'),(4,'collect_second_batch_of_20'),(5,'verification_criterion_not_met')]:
            self.assertEqual(e.verify_twenty([10]*(20-n)+[30]*n,5,20)['decision'],decision)
        self.assertEqual(e.verify_twenty([10]*17+[30]*3,5,20,batch=2,first_batch_outside=3)['decision'],'verification_criterion_not_met')
        with self.assertRaises(ValueError):e.verify_twenty([10]*19,5,20)
        with self.assertRaises(ValueError):e.verify_twenty([10]*20,5,20,batch=2,first_batch_outside=2)


class PluginTests(unittest.TestCase):
    def test_requested_method_is_never_changed_by_sample_size(self):
        for n in [40,120]:
            result=run(dataset(np.linspace(8,12,n)),METHOD='PARAMETRIC',CI_METHOD='NONE')
            self.assertEqual(result.table.iloc[0].method,'PARAMETRIC')
            self.assertEqual(result.table.iloc[0].ci_method,'not_requested')

    def test_nominal_and_unknown_explicit_selection_rejected(self):
        ds=dataset(np.arange(40.))
        ds.columns[1]=ColumnSpec('measurement','measurement',['RESULT','NUM','NOMINAL'])
        with self.assertRaises(ValueError):run(ds)
        with self.assertRaises(ValueError):run(dataset(range(40)),RESULT_IDS=['missing'])

    def test_censoring_needs_policy_and_exclusions_remain_visible(self):
        ds=dataset(np.linspace(8,12,120));ds.df.loc[0,'measurement']='<8'
        with self.assertRaises(ValueError):run(ds)
        result=run(ds,CENSOR_POLICY='EXCLUDE',CI_METHOD='RANK')
        row=result.table.iloc[0]
        self.assertEqual(row.n,119)
        self.assertEqual(row.censored_n,1)
        self.assertEqual(row.status,'censoring_sensitivity_only')
        self.assertIn('CENSORED_EXCLUDED',result.tables['input_audit'].reason.tolist())

    def test_repeated_individual_and_mixed_units_rejected(self):
        ds=dataset(range(120));ds.df.loc[1,'person']='0'
        with self.assertRaises(ValueError):run(ds)
        ds=dataset(range(120));ds.df['unit']=['mg/dL']*119+['mmol/L']
        ds.columns.append(ColumnSpec('unit','unit',['STR']))
        ds.meta['COLUMN']['unit']={'ID':'unit'};ds.meta['COLUMN']['measurement']['UNIT_ID']='unit'
        with self.assertRaises(ValueError):run(ds)

    def test_outlier_flag_and_remove_audit_preserve_original(self):
        ds=dataset([*np.linspace(8,12,120),100]);before=ds.df.copy(deep=True)
        flagged=run(ds,OUTLIER_METHOD='TUKEY',CI_METHOD='RANK')
        removed=run(ds,OUTLIER_METHOD='TUKEY',OUTLIER_ACTION='REMOVE',CI_METHOD='RANK')
        self.assertEqual(flagged.table.iloc[0].n,121)
        self.assertEqual(removed.table.iloc[0].n,120)
        self.assertEqual(removed.tables['outliers'].iloc[0].source_row,122)
        pd.testing.assert_frame_equal(ds.df,before)

    def test_partition_membership_and_missing_factor_denominators(self):
        ds=dataset(np.linspace(8,12,240),sex=True);ds.df.loc[0,'sex']=None
        result=run(ds,PARTITION_BY=['SEX'],CI_METHOD='RANK')
        self.assertEqual(sorted(result.table.n.tolist()),[119,120,240])
        self.assertEqual(set(result.table.partition_context_missing_n),{1})
        self.assertEqual(len(result.tables['partition_tests']),1)
        self.assertEqual(len(result.tables['partition_tails']),4)

    def test_names_and_column_order_do_not_change_statistical_results(self):
        ds=dataset(np.linspace(8,12,240),sex=True)
        first=run(ds,PARTITION_BY=['SEX'],CI_METHOD='RANK')
        other=deepcopy(ds)
        other.df=other.df.rename(columns={'measurement':'renamed'})[['sex','renamed','person']]
        other.columns=[ColumnSpec('sex','sex',['SEX','CATEGORY']),ColumnSpec('renamed','renamed',['RESULT','NUM']),ColumnSpec('person','person',['ID'])]
        other.meta['COLUMN']['renamed']=other.meta['COLUMN'].pop('measurement')
        second=run(other,PARTITION_BY=['SEX'],CI_METHOD='RANK')
        pd.testing.assert_frame_equal(first.table,second.table)

    def test_unknown_options_are_not_silently_ignored(self):
        with self.assertRaises(ValueError):run(dataset(range(120)),METHDO='ROBUST')
        with self.assertRaises(ValueError):run(dataset(range(120)),OUTLIER_METHOD='DIXON')

    def test_too_small_nonparametric_rank_resolution(self):
        result=run(dataset(np.linspace(8,12,20)),CI_METHOD='NONE')
        self.assertEqual(result.table.iloc[0].status,'insufficient_order_resolution')
        self.assertTrue(pd.isna(result.table.iloc[0].ref_low))

    def test_verification_qualified_population_and_censoring(self):
        options=dict(METHOD='PARAMETRIC',OUTLIER_METHOD='REED',CI_METHOD='NONE',
            VERIFY={'calcium':dict(LOW=8,HIGH=12,UNIT='mg/dL',REFERENCE='Synthetic fixed bounds; algorithm test')},
            POPULATION=dict(REFERENCE_INDIVIDUALS_CONFIRMED=True,DESCRIPTION='Synthetic validation',
                SELECTION_CRITERIA='Fixed fixture',PREANALYTICAL='Not clinical',ANALYTICAL='Generated'))
        result=run(dataset(np.linspace(9,11,20)),**options)
        self.assertEqual(result.tables['verification'].iloc[0].decision,'verification_criterion_met')
        ds=dataset([*np.linspace(9,11,20),'<8'])
        result=run(ds,CENSOR_POLICY='EXCLUDE',**options)
        self.assertEqual(result.tables['verification'].iloc[0].status,'not_evaluable')
        self.assertIn('Censored',result.tables['verification'].iloc[0].notes)
        ds=dataset([*np.linspace(9,11,19),100])
        result=run(ds,**options)
        self.assertEqual(result.tables['verification'].iloc[0].status,'outlier_review_and_replacement_required')

    def test_second_batch_rejects_reused_subjects(self):
        options=dict(METHOD='PARAMETRIC',OUTLIER_METHOD='REED',CI_METHOD='NONE',VERIFY_BATCH=2,FIRST_BATCH_OUTSIDE=3,
            VERIFY={'calcium':dict(LOW=8,HIGH=12,UNIT='mg/dL',REFERENCE='Synthetic fixed bounds')},
            POPULATION=dict(REFERENCE_INDIVIDUALS_CONFIRMED=True,DESCRIPTION='Synthetic validation',
                SELECTION_CRITERIA='Fixed fixture',PREANALYTICAL='Not clinical',ANALYTICAL='Generated'))
        ds=dataset(np.linspace(9,11,20))
        with self.assertRaises(ValueError):run(ds,**options)
        result=run(ds,FIRST_BATCH_SUBJECT_IDS=[str(i) for i in range(20)],**options)
        self.assertEqual(result.tables['verification'].iloc[0].status,'not_evaluable')
        self.assertIn('first batch',result.tables['verification'].iloc[0].notes)
        result=run(ds,FIRST_BATCH_SUBJECT_IDS=[str(i) for i in range(20,40)],**options)
        self.assertEqual(result.tables['verification'].iloc[0].decision,'verification_criterion_met')

    def test_empty_cohort_and_invalid_integer_option(self):
        result=run(dataset([]),CI_METHOD='NONE')
        self.assertTrue(result.table.empty)
        with self.assertRaises(ValueError):run(dataset(range(120)),BOOTSTRAP_N=120.5)
        with self.assertRaises(ValueError):run(dataset(range(120)),VERIFY={'typo':{}})

    def test_report_artifacts_and_chainable_tame(self):
        ds=dataset(np.linspace(8,12,120))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'new_report'
            result=run(ds,CI_METHOD='RANK',OUTPUT_DIR=str(path),PLOT_MAX_GROUPS=1)
            text=(path/'reference_interval_report_ko.html').read_text(encoding='utf-8')
            self.assertIn('해석상 주의사항',text)
            self.assertIn('data:image/png;base64,',text)
            self.assertIn('exploratory_only',text)
            self.assertEqual(len(read_tame(path/'reference_intervals.tame').df),1)
            with zipfile.ZipFile(path/'reference_interval_report_ko.docx') as z:
                self.assertTrue(any(p.startswith('word/media/') for p in z.namelist()))
            with self.assertRaises(ValueError):run(ds,CI_METHOD='RANK',OUTPUT_DIR=str(path))


if __name__=='__main__':unittest.main(verbosity=2)
