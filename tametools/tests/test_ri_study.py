"""Public-study extensions: invariants, manual examples, exclusions and logging."""
import unittest
import numpy as np
import pandas as pd
from scipy import stats
from tametools import ri_ep28 as e,ri_study as s
from tametools.models import ColumnSpec,TameDataset
from tametools.plugin_base import run_plugin

class StudyMethodsTests(unittest.TestCase):
    def test_fixed_origin_matches_log_and_is_affine_equivariant(self):
        x=4+np.exp(np.linspace(.5,2.5,120))
        a=e.estimate_limits(x,'BOXCOX_SHIFTED_PARAMETRIC',boxcox={'ORIGIN':4})
        b=e.estimate_limits(x-4,'BOXCOX_PARAMETRIC')
        np.testing.assert_allclose([a.low,a.high],np.array([b.low,b.high])+4,atol=2e-5)
        fit=e.estimate_limits(x,'BOXCOX_SHIFTED_PARAMETRIC')
        shifted=e.estimate_limits(88.4*x+20,'BOXCOX_SHIFTED_PARAMETRIC')
        np.testing.assert_allclose([shifted.low,shifted.high],88.4*np.array([fit.low,fit.high])+20,atol=.003)
    def test_invalid_origin_and_negative_values(self):
        with self.assertRaises(ValueError):e.estimate_limits([1,2,3],'BOXCOX_SHIFTED_PARAMETRIC',boxcox={'ORIGIN':1})
        v=e.estimate_limits(np.linspace(-5,5,100),'BOXCOX_SHIFTED_PARAMETRIC',boxcox={'ORIGIN':-6})
        self.assertTrue(np.isfinite([v.low,v.high]).all())
    def test_gaussian_trim_is_one_pass_and_tracks_original_positions(self):
        x=np.r_[np.linspace(8,12,120),100.]
        mask,details=s.gaussian_trim(x,'PARAMETRIC',2.81)
        self.assertEqual(np.flatnonzero(mask).tolist(),[120]);self.assertEqual(details['passes'],1)
    def test_lave_never_screens_target_itself(self):
        x=np.tile(np.linspace(10,20,120)[:,None],(1,3));x[-1]=[1000,15,15]
        r=s.lave(x,['A','B','C'],targets=['A','D'],method='NONPARAMETRIC',max_abnormal=0,iterations=2)
        self.assertTrue(r['masks']['A'][-1]);self.assertFalse(r['masks']['D'][-1])
        self.assertEqual(r['abnormal_counts']['A'][-1],0)
    def test_lave_missing_panel_is_not_normal(self):
        x=np.tile(np.linspace(10,20,120)[:,None],(1,3));x[5,1]=np.nan
        with self.assertRaises(ValueError):s.lave(x,['A','B','C'],targets=['A'])
        r=s.lave(x,['A','B','C'],targets=['A'],method='NONPARAMETRIC',missing_policy='EXCLUDE')
        self.assertFalse(r['masks']['A'][5]);self.assertFalse(r['complete'][5])
    def test_sdr_manual_balanced_anova(self):
        # Two groups n=3, means 2/6, pooled within variance=1.
        r=s.variance_sdr([1,2,3,5,6,7],['A']*3+['B']*3)
        self.assertAlmostEqual(r['residual_variance'],1)
        self.assertAlmostEqual(r['between_variance'],(24-1)/3)
        self.assertAlmostEqual(r['sdr'],np.sqrt(23/3))
        nested=s.variance_sdr([0,2,4,6,10,12,14,16],['A']*4+['B']*4,['young','young','old','old']*2)
        self.assertAlmostEqual(nested['residual_variance'],2)
        self.assertAlmostEqual(nested['nested_variance'],7)
        self.assertAlmostEqual(nested['between_variance'],46)
    def test_bias_ratio_uses_pooled_ri_width(self):
        r=s.bias_ratios([10,20],[12,24],[9,25],1)
        self.assertAlmostEqual(r['br_low'],2/(16/3.92))
        self.assertFalse(r['delta_low_ge_3ru']);self.assertTrue(r['delta_high_ge_3ru'])
    def test_fifty_bootstrap_mean_and_interpolation(self):
        x=np.arange(1.,121.)
        ix=np.random.default_rng(7).integers(0,len(x),(50,len(x)))
        values=e.bootstrap_limits(x,'NONPARAMETRIC',indices=ix)
        ci=e.reference_ci(x,'NONPARAMETRIC',ci_method='BOOTSTRAP',indices=ix,bootstrap_quantile='R_BOOT')
        np.testing.assert_allclose(ci['bootstrap_mean'],values.mean(axis=0))
        self.assertEqual(ci['bootstrap_valid'],50)
        self.assertNotEqual(e.r_boot_quantile(np.arange(1.,51.),[.05,.95]),list(np.quantile(np.arange(1.,51.),[.05,.95])))
    def test_plugin_lave_and_study_diagnostics_are_auditable(self):
        n=240;x=np.linspace(10,20,n);frame=pd.DataFrame({'id':[str(i) for i in range(n)],'sex':['F']*120+['M']*120,'A':x,'B':x,'C':x})
        frame.loc[n-1,['A','B']]=1000
        specs=[ColumnSpec(k,k,['ID'] if k=='id' else ['SEX','CATEGORY'] if k=='sex' else ['RESULT','NUM']) for k in frame]
        meta={'COLUMN':{k:{'ID':k,**({'UNIT':'U/L'} if k in ['A','B','C'] else {})} for k in frame}}
        ds=TameDataset(frame.astype(str),specs,meta)
        out=run_plugin(ds,'RI_EP28',meta,'check',{'SUBJECT_ID':'id','RESULT_IDS':['A'], 'PARTITION_BY':['SEX'],'SEX_ID':'sex',
            'LAVE':{'REFERENCE_IDS':['A','B','C'],'TARGET_IDS':['A'],'METHOD':'NONPARAMETRIC','MAX_ABNORMAL':0},
            'PARTITION_DIAGNOSTICS':['WILCOXON','SDR_BR'],'REPORTING_UNITS':{'A':1},'NORMALITY_TESTS':['KS_LILLIEFORS']})
        self.assertFalse(out.tables['lave_membership'].query("subject_id=='239'").included.iloc[0])
        self.assertTrue(out.tables['lave_iterations'].shape[0]>0)
        self.assertIn('wilcoxon_p',out.tables['partition_tests'])
        self.assertIn('sdr',out.tables['partition_tests'])
        self.assertGreater(out.table.lave_removed.max(),0)
        self.assertIn('LAVE',out.dataset.meta['RI_EP28_RESULT']['SETTINGS_JSON'])
    def test_bootstrap_mean_plugin_is_actual_resample_average(self):
        x=np.arange(1.,121.);frame=pd.DataFrame({'value':x.astype(str)})
        ds=TameDataset(frame,[ColumnSpec('value','value',['RESULT','NUM'])],{'COLUMN':{'value':{'ID':'v','UNIT':'U/L'}}})
        out=run_plugin(ds,'RI_EP28',ds.meta,'check',{'BOOTSTRAP_N':50,'CI_METHOD':'BOOTSTRAP','POINT_ESTIMATE':'BOOTSTRAP_MEAN','SEED':9})
        expected=e.bootstrap_limits(x,'NONPARAMETRIC',repetitions=50,seed=9).mean(axis=0)
        np.testing.assert_allclose(out.table[['ref_low','ref_high']].iloc[0].to_numpy(float),expected)
        self.assertIn('Fewer than 100',out.table.notes.iloc[0])

if __name__=='__main__':unittest.main()
