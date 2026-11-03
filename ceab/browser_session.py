"""Chrome/Chromium do sistema com perfil persistente, sem porta CDP pública."""
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse


def find_browser():
    candidates = [shutil.which(name) for name in ('google-chrome','google-chrome-stable','chrome','chromium','chromium-browser')]
    candidates += ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                   os.path.expandvars(r'%PROGRAMFILES%\Google\Chrome\Application\chrome.exe'),
                   os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe')]
    return next((str(Path(path).resolve()) for path in candidates if path and Path(path).is_file()),None)


def open_persistent(playwright,config):
    profile = Path(config['profile_path'])
    profile.mkdir(parents=True,exist_ok=True,mode=0o700)
    # O perfil Default é sempre o mesmo. A preferência nativa conserva também
    # cookies de sessão e abas, inclusive quando a janela é fechada pelo usuário.
    preferences = profile/'Default'/'Preferences'
    preferences.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    data = json.loads(preferences.read_text()) if preferences.exists() else {}
    data.setdefault('profile',{}).setdefault('name','CEAB/DJ')
    data.setdefault('session',{})['restore_on_startup'] = 1
    preferences.write_text(json.dumps(data))
    preferences.chmod(0o600)
    options = {'headless':bool(config['headless']),
               'args':['--disable-popup-blocking','--restore-last-session','--profile-directory=Default']}
    if config.get('executable_path'): options['executable_path'] = config['executable_path']
    context = playwright.chromium.launch_persistent_context(str(profile),**options)
    # Não substituir uma aba autenticada restaurada pela página inicial/login.
    origin = urlparse(config['start_url']).netloc
    restored = next((p for p in context.pages if urlparse(p.url).netloc == origin),None)
    from playwright.sync_api import Error as BrowserError
    try:
        if restored:
            restored.wait_for_load_state('domcontentloaded',timeout=30000)
            restored.bring_to_front()
        else:
            page = next((p for p in context.pages if p.url == 'about:blank'),None) or context.new_page()
            page.goto(config['start_url'],wait_until='domcontentloaded',timeout=30000)
    except BrowserError:
        # Falha de rede ou espera por certificado não deve destruir a janela
        # antes que a pessoa possa fazer login/navegar manualmente.
        pass
    return context
