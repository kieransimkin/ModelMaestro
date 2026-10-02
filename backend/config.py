from __future__ import annotations
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]

def atomic_json(path: Path, value) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    tmp.chmod(0o600)
    tmp.replace(path)

@dataclass
class Settings:
    state: Path = field(default_factory=lambda: Path(os.getenv('APP_STATE_DIR', ROOT / '.state')).expanduser().resolve())
    origin: str = field(default_factory=lambda: os.getenv('APP_ORIGIN', 'http://127.0.0.1:3000').rstrip('/'))
    auto_install: bool = field(default_factory=lambda: os.getenv('AUTO_INSTALL', '1') == '1')
    demo: bool = field(default_factory=lambda: os.getenv('DEMO_MODE', '0') == '1')
    token: str = ''
    def __post_init__(self):
        self.state.mkdir(parents=True, exist_ok=True)
        self.state.chmod(0o700)
        for folder in ('runtimes', 'models', 'cache', 'mcp', 'logs'):
            (self.state / folder).mkdir(exist_ok=True)
        token_file = self.state / 'access-token'
        self.token = self.token or os.getenv('APP_TOKEN', '')
        if not self.token:
            if not token_file.exists():
                # Exclusive creation avoids two launches replacing each other's secret.
                with token_file.open('x') as f:
                    token_file.chmod(0o600)
                    f.write(secrets.token_urlsafe(36))
            self.token = token_file.read_text().strip()
        if len(self.token) < 24:
            raise ValueError('APP_TOKEN must contain at least 24 characters')
        parsed = urlsplit(self.origin)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.path:
            raise ValueError('APP_ORIGIN must be an http(s) origin without a path')
    @property
    def origins(self):
        port = urlsplit(self.origin).port or (443 if self.origin.startswith('https:') else 80)
        return list({self.origin, f'http://127.0.0.1:{port}', f'http://localhost:{port}'})
    def redact(self, text: str) -> str:
        values = [self.token] + [v for k, v in os.environ.items()
                if len(v) >= 8 and re.search(r'(TOKEN|SECRET|PASSWORD|API_KEY)$', k)]
        for value in values:
            text = text.replace(value, '[REDACTED]')
        return re.sub(r'(?i)(bearer\s+)[\w.\-]+', r'\1[REDACTED]', text)

def within(root: Path, relative: str) -> Path:
    if not relative or '\\' in relative or '\x00' in relative:
        raise ValueError('Invalid relative path')
    candidate = (root / relative).resolve()
    if candidate == root.resolve() or root.resolve() not in candidate.parents:
        raise ValueError('Path escapes its managed directory')
    return candidate
