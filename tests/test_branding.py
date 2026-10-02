"""Regression checks for ModelMaestro's application identity and startup contract."""
import ast
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import start

ROOT = Path(__file__).resolve().parents[1]


class BrandingTests(unittest.TestCase):
    def test_package_and_page_use_new_name(self):
        manifest = json.loads((ROOT / 'package.json').read_text())
        self.assertEqual(manifest['name'], 'modelmaestro')
        self.assertEqual(manifest['version'], '0.1.0')
        self.assertIn("title: 'ModelMaestro'", (ROOT / 'app/layout.tsx').read_text())
        self.assertIn("@/components/ModelMaestro", (ROOT / 'app/page.tsx').read_text())
        component = (ROOT / 'components/ModelMaestro.tsx').read_text()
        self.assertIn('function ModelMaestro()', component)
        self.assertIn('<strong>Model<em>Maestro</em></strong>', component)
        self.assertIn('DANCEFLOW', component)
        self.assertNotIn('oLLM <em>Console</em>', component)
        self.assertFalse((ROOT / 'components/Console.tsx').exists())

    def test_health_identity_and_launcher_readiness_agree(self):
        # Inspect the actual route without importing optional Socket.IO dependencies.
        tree = ast.parse((ROOT / 'backend/app.py').read_text())
        route = next(node for node in ast.walk(tree)
                     if isinstance(node, ast.AsyncFunctionDef) and node.name == 'health')
        returned = next(node for node in ast.walk(route) if isinstance(node, ast.Return))
        payload = ast.literal_eval(returned.value)
        self.assertEqual(payload['service'], 'modelmaestro')
        import io
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
        opener.open.return_value.__exit__ = Mock(return_value=False)
        process = Mock()
        process.poll.return_value = None
        with patch.object(start.urllib.request, 'build_opener', return_value=opener), \
                patch.object(start.time, 'sleep', side_effect=AssertionError('Readiness identity mismatch')):
            start.wait_for_backend(process, 8765)
        opener.open.assert_called_once_with('http://127.0.0.1:8765/api/health', timeout=1)

    def test_systemd_example_uses_new_path(self):
        service = (ROOT / 'examples/modelmaestro.service').read_text()
        self.assertIn('Description=ModelMaestro', service)
        self.assertIn('WorkingDirectory=/absolute/path/modelmaestro', service)
        self.assertIn('/absolute/path/modelmaestro/start.py', service)
        self.assertFalse((ROOT / 'examples/ollm-console.service').exists())
        self.assertIn('examples/modelmaestro.service', (ROOT / 'README.md').read_text())

    def test_socket_example_preserves_legacy_environment_fallback(self):
        client = (ROOT / 'examples/socket-client.mjs').read_text()
        self.assertIn('process.env.MODELMAESTRO_TOKEN || process.env.CONSOLE_TOKEN', client)
        self.assertIn('process.env.MODELMAESTRO_URL || process.env.CONSOLE_URL', client)
        self.assertIn('MODELMAESTRO_TOKEN=', (ROOT / 'README.md').read_text())


if __name__ == '__main__':
    unittest.main()
