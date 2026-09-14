import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from tametools.bootstrap import init_from_table, infer_column_tags
from tametools.cellstate import cell_state
from tametools.cli import main
from tametools.io import read_tame
from tametools.provenance_audit import verify_history
from tametools.toml_compat import dumps, loads
from test_init_review import SCOPED


class InitSemanticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.input = self.root / 'input.csv'
        self.output = self.root / 'out.tame'

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, data, definitions=None, answers=None, output=None):
        pd.DataFrame(data).to_csv(self.input, index=False)
        definition_path = None
        if definitions is not None:
            definition_path = self.root / 'config.toml'
            definition_path.write_text(dumps(definitions) if isinstance(definitions, dict) else definitions, encoding='utf-8')
        choices = iter(answers or [])
        self.transcript = []
        def ask(prompt):
            value = next(choices)
            self.transcript.append(prompt + value)
            return value
        result = init_from_table(self.input, output or self.output, definitions_path=definition_path,
                                 interactive=answers is not None, input_fn=ask, output_fn=self.transcript.append)
        return read_tame(output or self.output), result

    def test_full_scan_catches_late_unknown_text_and_age(self):
        _, result = self.build({'SEX': ['M'] * 2000 + ['unrecorded'], 'AGE': ['42'] * 2000 + ['40-49']})
        profiles = result['review']['input_profiles']
        self.assertTrue(all(p['rows_scanned'] == 2001 and p['unrecognized_cells'] == 1 for p in profiles))
        self.assertEqual(profiles[1]['invalid_row_examples'], [2002])
        self.assertEqual({i['code'] for i in result['review']['issues']}, {'SEX_VALUE_MAP', 'AGE_VALUE_INVALID'})

    def test_empty_semantic_columns_are_not_validated_from_name_alone(self):
        _, result = self.build({'SEX': ['', ''], 'AGE': ['', '']})
        self.assertEqual(result['review']['required_definitions'], 2)

    def test_scoped_interactive_mapping_and_replay_preserve_raw_and_log(self):
        data = {'source': ['A', 'A', 'B', 'B'], 'SEX': ['0', '1', '1', '2']}
        ds, result = self.build(data, answers=['y', 'y', 'male', 'female', 'male', 'female'])
        self.assertEqual(ds.df.SEX.tolist(), ['male', 'female', 'male', 'female'])
        self.assertEqual(ds.df.SEX__raw.tolist(), ['0', '1', '1', '2'])
        self.assertEqual(result['review']['required_definitions'], 0)
        replay = self.root / 'replay.tame'
        rr = init_from_table(self.input, replay, definitions_path=Path(result['decisions_path']))
        pd.testing.assert_frame_equal(ds.df, read_tame(replay).df)
        self.assertEqual(rr['review']['required_definitions'], 0)
        self.assertEqual(rr['review']['decisions_sha256'], result['review']['decisions_sha256'])
        self.assertEqual(hashlib.sha256(Path(result['decisions_path']).read_bytes()).hexdigest(), result['review']['decisions_sha256'])
        for item in (ds, read_tame(replay)):
            audit = verify_history(item)
            self.assertTrue(all(audit['checks'][k] == 'PASS' for k in ('history', 'data', 'controls')), audit)
        self.assertEqual(ds.meta['INIT']['TRANSFORMATIONS'][0]['changed_cells'], 4)

    def test_numeric_codes_are_never_guessed_when_declined(self):
        ds, result = self.build({'SEX': ['0', '1', '2']}, answers=['y', 'n'])
        self.assertEqual(ds.df.SEX.tolist(), ['0', '1', '2'])
        self.assertNotIn('SEX__raw', ds.df.columns)
        self.assertEqual(result['review']['required_definitions'], 1)

    def test_partial_mapping_retains_unmapped_value_and_required_review(self):
        ds, result = self.build({'SEX': ['M', '2', '9']}, answers=['y', 'y', '', 'female', 'skip'])
        self.assertEqual(ds.df.SEX.tolist(), ['male', 'female', '9'])
        self.assertEqual(result['review']['transformations'][0]['unmapped_cells'], 1)
        self.assertEqual(result['review']['required_definitions'], 1)
        template = loads(Path(result['definitions_template']).read_text(encoding='utf-8'))
        self.assertNotIn('SEX__raw', template['COLUMN'])
        self.assertEqual(template['CATEGORIES']['SEX_CODES_2']['MAP']['M'], 'male')

    def test_noncanonical_text_can_be_mapped_and_bad_choice_reprompts(self):
        ds, _ = self.build({'SEX': ['woman', 'nonstandard']}, answers=['y', 'y', '', '3', 'other'])
        self.assertEqual(ds.df.SEX.tolist(), ['female', 'other'])
        self.assertTrue(any('선택 가능한 값' in s for s in self.transcript))

    def test_select_arbitrary_source_column(self):
        ds, _ = self.build({'cohort': ['A', 'B'], 'SEX': ['1', '1']}, answers=['y', 'y', 'cohort', 'female', 'male'])
        self.assertEqual(ds.df.SEX.tolist(), ['female', 'male'])

    def test_unknown_source_never_uses_global_numeric_mapping(self):
        defs = SCOPED + '\n[INIT_OPTIONS.SEX.Sex]\nNORMALIZE=true\n'
        ds, result = self.build({'source': ['A', 'B', 'C', ''], 'Sex': ['1', '1', '1', '1']}, defs)
        self.assertEqual(ds.df.Sex.tolist(), ['female', 'male', '1', '1'])
        self.assertEqual(result['review']['transformations'][0]['unmapped_cells'], 2)

    def test_semantic_header_false_positive_can_be_rejected(self):
        ds, result = self.build({'SEX': ['categorization'], 'AGE': ['18-29']}, answers=['n', 'n'])
        self.assertTrue(all(c.tags == ('STR',) for c in ds.columns))
        self.assertEqual(result['review']['required_definitions'], 0)
        self.assertEqual(result['review']['output_profiles'], [])

    def test_existing_group_names_are_categorical_not_individual_age(self):
        for name in ('AGE_5', 'AGE_10', 'age_group', 'AgeBand', '연령군'):
            tags = infer_column_tags(name, pd.Series(['1-4', '5-9']))
            self.assertIn('AGE_GROUP', tags, name)
            self.assertNotIn('AGE', tags, name)
        self.assertNotIn('SEX', infer_column_tags('Sussex', pd.Series(['a', 'b'])))

    def test_age_boundaries_month_day_and_missing_states(self):
        ages = ['0', '.999', '1', '4.999', '5', '9.999', '10', '69.999', '70', '6mo', '365.25d', '', '<<NULL>>', '<<EMPTY>>', '<<WS:2>>']
        ds, result = self.build({'AGE': ages}, answers=['y', 'a', 'n', 'both'])
        self.assertEqual(ds.df.AGE_5.tolist()[:11], ['<1', '<1', '1-4', '1-4', '5-9', '5-9', '10-14', '65-69', '70+', '<1', '1-4'])
        self.assertEqual(ds.df.AGE_10.tolist()[:11], ['<1', '<1', '1-9', '1-9', '1-9', '1-9', '10-19', '60-69', '70+', '<1', '1-9'])
        self.assertEqual(ds.df.AGE.tolist()[:11], ages[:11])
        self.assertEqual([cell_state(v) for v in ds.df.AGE_5.iloc[11:]], ['ABSENT', 'NULL', 'EMPTY', 'WS'])
        self.assertEqual(ds.column_metadata('AGE_5')['DERIVED_FROM'], 'AGE')
        self.assertEqual(ds.columns_with_tag('AGE')[0].name, 'AGE')
        self.assertEqual(len(ds.columns_with_tag('AGE')), 1)
        self.assertTrue(all(k not in loads(Path(result['definitions_template']).read_text())['COLUMN'] for k in ('AGE_5', 'AGE_10')))

    def test_declared_months_materialize_unit_and_preserve_raw(self):
        ds, result = self.build({'AGE': ['6', '12', '365.25d']}, answers=['y', 'mo', 'n', 'both'])
        self.assertEqual(ds.df.AGE.tolist(), ['6mo', '12mo', '365.25d'])
        self.assertEqual(ds.df.AGE__raw.tolist(), ['6', '12', '365.25d'])
        self.assertEqual(ds.df.AGE_5.tolist(), ['<1', '1-4', '1-4'])
        replay = self.root/'replay.tame'
        init_from_table(self.input, replay, definitions_path=Path(result['decisions_path']))
        pd.testing.assert_frame_equal(ds.df, read_tame(replay).df)

    def test_invalid_age_requires_explicit_group_null_policy(self):
        defs = {'INIT_OPTIONS': {'AGE_GROUPS': {'AGE': {'WIDTHS': [5]}}}}
        with self.assertRaisesRegex(ValueError, 'invalid ages'):
            self.build({'AGE': ['-1', 'unknown', '18-29', '20']}, defs)
        self.assertFalse(self.output.exists())
        ds, result = self.build({'AGE': ['-1', 'unknown', '18-29', '20']}, answers=['y', 'a', '5', 'y'])
        self.assertEqual([cell_state(v) for v in ds.df.AGE_5], ['NULL', 'NULL', 'NULL', 'VALUE'])
        self.assertEqual(ds.df.AGE.tolist(), ['-1', 'unknown', '18-29', '20'])
        self.assertEqual(result['review']['required_definitions'], 1)
        self.assertEqual(ds.column_metadata('AGE_5')['INVALID_CELLS'], 3)

    def test_declining_invalid_age_groups_preserves_data(self):
        ds, _ = self.build({'AGE': ['-1', '20']}, answers=['y', 'a', 'both', 'n'])
        self.assertEqual(list(ds.df.columns), ['AGE'])

    def test_two_age_columns_generate_distinct_groups(self):
        ds, result = self.build({'Age_years': ['0'], 'Age_with_unit': ['6mo']}, answers=['y', 'a', 'both', 'y', 'a', '5'])
        self.assertEqual(list(ds.df.columns), ['Age_years', 'Age_with_unit', 'Age_years_5', 'Age_years_10', 'Age_with_unit_5'])

    def test_mixed_age_units_can_add_completed_years_and_replay(self):
        ages = ['37', '7mo', '24mo', '365.25d', '<<NULL>>']
        ds, result = self.build({'AGE': ages}, answers=['y', 'a', 'y', 'none'])
        self.assertEqual(ds.df.AGE.tolist()[:4], ages[:4])
        self.assertEqual(cell_state(ds.df.AGE.iloc[4]), 'NULL')
        self.assertEqual(ds.df.AGE_INTEGER.tolist()[:4], ['37', '0', '2', '1'])
        self.assertEqual(cell_state(ds.df.AGE_INTEGER.iloc[4]), 'NULL')
        self.assertEqual(ds.column_metadata('AGE_INTEGER')['ROLE'], 'completed integer years')
        self.assertEqual(ds.column_metadata('AGE_INTEGER')['DERIVED_FROM'], 'AGE')
        self.assertEqual(ds.meta['INIT']['TRANSFORMATIONS'][0]['operation'], 'CREATE_INTEGER_AGE')
        issue = next(i for i in result['review']['issues'] if i['code'] == 'AGE_MIXED_UNITS')
        self.assertEqual(issue['status'], 'RESOLVED')
        replay = self.root / 'replay.tame'
        init_from_table(self.input, replay, definitions_path=Path(result['decisions_path']))
        pd.testing.assert_frame_equal(ds.df, read_tame(replay).df)

    def test_mixed_age_review_offers_integer_column_without_creating_it(self):
        ds, result = self.build({'AGE': ['42', '6mo']})
        self.assertNotIn('AGE_INTEGER', ds.df.columns)
        issue = next(i for i in result['review']['issues'] if i['code'] == 'AGE_MIXED_UNITS')
        self.assertEqual(issue['status'], 'NEEDS_DEFINITION')
        self.assertFalse(issue['required'])
        self.assertEqual(issue['observed_units'], {'a': 1, 'mo': 1})

    def test_integer_age_invalid_policy_and_output_collision_are_enforced(self):
        base = {'AGE_INTEGER': {'AGE': {'OUTPUT': 'AGE_INT', 'METHOD': 'FLOOR'}}}
        with self.assertRaisesRegex(ValueError, 'invalid ages'):
            self.build({'AGE': ['unknown']}, {'INIT_OPTIONS': base})
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.build({'AGE': ['6mo'], 'AGE_INT': ['keep']}, {'INIT_OPTIONS': base})

    def test_output_collision_rejected_without_overwriting_existing_output(self):
        self.output.write_text('keep existing file')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.build({'AGE': ['20'], 'AGE_5': ['20-24']}, {'INIT_OPTIONS': {'AGE_GROUPS': {'AGE': {'WIDTHS': [5]}}}})
        self.assertEqual(self.output.read_text(), 'keep existing file')
        self.assertFalse(self.output.with_suffix('.init-decisions.toml').exists())

    def test_invalid_config_cannot_silently_disable_or_change_analysis(self):
        configs = [ {'SEX': {'SEX': {'NORMALIZE': 'false'}}}, {'AGE': {'AGE': {'UNIT': 'week'}}},
                    {'AGE_INTEGER': {'AGE': {'OUTPUT': '', 'METHOD': 'FLOOR'}}},
                    {'AGE_INTEGER': {'AGE': {'OUTPUT': 'AGE_INT', 'METHOD': 'ROUND'}}},
                    {'AGE_GROUPS': {'AGE': {'WIDTHS': [True]}}}, {'AGE_GROUPS': {'AGE': {'WIDTHS': [5, 5]}}},
                    {'AGE_GROUPS': {'AGE': {'WIDTHS': [5], 'OPEN_UPPER': 71}}}, {'BOGUS': {}},
                    {'SEX': {'missing': {'NORMALIZE': True}}} ]
        for config in configs:
            with self.subTest(config=config), self.assertRaises(ValueError):
                self.build({'SEX': ['M'], 'AGE': ['20']}, {'INIT_OPTIONS': config})

    def test_decisions_do_not_promote_default_comparator_to_reviewed(self):
        _, result = self.build({'SEX': ['M'], 'result': ['<1']}, answers=['y', 'n'])
        self.assertEqual(result['review']['required_definitions'], 1)
        rr = init_from_table(self.input, self.root/'replay.tame', definitions_path=Path(result['decisions_path']))
        self.assertEqual(rr['review']['required_definitions'], 1)

    def test_edited_decisions_file_is_preserved(self):
        _, result = self.build({'SEX': ['M']})
        path = Path(result['decisions_path'])
        path.write_text('# user edit', encoding='utf-8')
        _, result2 = self.build({'SEX': ['M']})
        self.assertEqual(path.read_text(), '# user edit')
        self.assertNotEqual(result['decisions_path'], result2['decisions_path'])

    def test_conflicting_case_insensitive_mappings_are_rejected(self):
        defs = {'COLUMN': {'SEX': {'TAGS': ['SEX', 'CATEGORY', 'CODES']}},
                'CATEGORIES': {'CODES': {'VALUES': ['male', 'female'], 'MAP': {'1': 'male', ' 1 ': 'female'}}}}
        with self.assertRaisesRegex(ValueError, 'Conflicting sex mappings'):
            self.build({'SEX': ['1', ' 1 ']}, defs)

    def test_cli_auto_interactive_and_noninteractive_modes(self):
        pd.DataFrame({'SEX': ['M', 'F']}).to_csv(self.input, index=False)
        args = ['tametools', 'init', str(self.input), '--output', str(self.output)]
        with patch.object(sys, 'argv', args), patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['y', 'y', '']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        self.assertEqual(read_tame(self.output).df.SEX.tolist(), ['male', 'female'])
        for tty, extra in ((False, []), (True, ['--no-interactive'])):
            with patch.object(sys, 'argv', args + extra), patch('sys.stdin.isatty', return_value=tty), patch('builtins.input', side_effect=AssertionError('Unexpected prompt')), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            self.assertEqual(read_tame(self.output).df.SEX.tolist(), ['M', 'F'])

    def test_cli_explicit_interactive_supports_scripted_input(self):
        pd.DataFrame({'AGE': ['6mo']}).to_csv(self.input, index=False)
        args = ['tametools', 'init', str(self.input), '--output', str(self.output), '--interactive']
        with patch.object(sys, 'argv', args), patch('sys.stdin.isatty', return_value=False), patch('builtins.input', side_effect=['y', 'a', 'both']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        self.assertEqual(read_tame(self.output).df.AGE_5.tolist(), ['<1'])

    def test_cli_require_reviewed_exits_two_after_writing_report(self):
        pd.DataFrame({'SEX': ['1']}).to_csv(self.input, index=False)
        args = ['tametools', 'init', str(self.input), '--output', str(self.output), '--no-interactive', '--require-reviewed']
        with patch.object(sys, 'argv', args), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 2)
        self.assertEqual(json.loads(self.output.with_suffix('.init-review.json').read_text())['required_definitions'], 1)

    def test_interactive_eof_aborts_before_writing(self):
        pd.DataFrame({'SEX': ['0']}).to_csv(self.input, index=False)
        with self.assertRaisesRegex(ValueError, 'interrupted'):
            init_from_table(self.input, self.output, interactive=True, input_fn=lambda _: (_ for _ in ()).throw(EOFError), output_fn=lambda _: None)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
