"""Standard-library tests for installation, interruption recovery and local ports."""
import importlib.util,json,os
from pathlib import Path
import socket,sys,tempfile,unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('browser_launcher',ROOT/'scripts/start_tametools_browser.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)


class BrowserLauncherTests(unittest.TestCase):
    def test_packaged_frontend_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError,'ZIP 전체'):launcher.check_source(Path(tmp))

    def test_running_same_app_is_reused(self):
        with patch.object(launcher,'read_status',return_value={'application':launcher.APPLICATION,'instance':'ours'}):
            self.assertEqual(launcher.choose_port(8765,'ours'),(8765,True))

    def test_unrelated_service_is_not_reused(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',0));port=occupied.getsockname()[1]
            if port>65515:self.skipTest('OS selected a port outside the launcher range')
            with patch.object(launcher,'read_status',return_value={'application':'other','instance':'ours'}):
                chosen,reused=launcher.choose_port(port,'ours')
            self.assertGreater(chosen,port);self.assertFalse(reused)

    def test_bad_port_is_rejected(self):
        for port in [-1,80,65535]:
            with self.subTest(port=port),self.assertRaises(ValueError):launcher.choose_port(port,'x')

    def test_valid_environment_skips_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime=Path(tmp);python=launcher.environment_python(runtime)
            python.parent.mkdir(parents=True);python.touch()
            (runtime/'environment.json').write_text(json.dumps({'fingerprint':'same'}))
            with patch.object(launcher,'fingerprint',return_value='same'),patch.object(launcher,'run') as install:
                self.assertEqual(launcher.prepare_environment(runtime),python)
                install.assert_not_called()

    def test_failed_setup_can_retry_without_stale_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime=Path(tmp);python=launcher.environment_python(runtime)
            python.parent.mkdir(parents=True);python.touch()
            with patch.object(launcher,'fingerprint',return_value='changed'),patch.object(launcher,'run',side_effect=RuntimeError('offline')):
                with self.assertRaisesRegex(RuntimeError,'offline'):launcher.prepare_environment(runtime)
            self.assertFalse((runtime/'environment.json').exists())
            # The file remains, but its advisory lock has been released.
            with launcher.lock_setup(runtime/'setup.lock'):pass

    def test_parallel_setup_is_rejected_and_lock_can_be_reacquired(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'setup.lock'
            with launcher.lock_setup(path):
                with self.assertRaisesRegex(RuntimeError,'다른 시작 창'):launcher.lock_setup(path)
            with launcher.lock_setup(path):pass

    def test_prepare_only_does_not_need_a_listening_socket(self):
        with patch.object(launcher,'check_source'),patch.object(launcher,'instance_id',return_value='x'),\
             patch.object(launcher,'choose_port',side_effect=AssertionError('No network during setup')),\
             patch.object(launcher,'prepare_environment') as install:
            self.assertEqual(launcher.main(['--prepare-only']),0)
            install.assert_called_once()

    def test_repair_cannot_remove_running_environment(self):
        with patch.object(launcher,'check_source'),patch.object(launcher,'instance_id',return_value='x'),\
             patch.object(launcher,'choose_port',return_value=(8765,True)),\
             patch.object(launcher,'prepare_environment') as install:
            self.assertEqual(launcher.main(['--repair']),1)
            install.assert_not_called()

    def test_explicit_chrome_path_supports_spaces(self):
        with tempfile.TemporaryDirectory(prefix='chrome path ') as tmp:
            path=Path(tmp)/'chrome';path.touch()
            with patch.dict(os.environ,{'TAMETOOLS_CHROME':str(path)}):self.assertEqual(launcher.find_chrome(),str(path))

if __name__=='__main__':unittest.main()
