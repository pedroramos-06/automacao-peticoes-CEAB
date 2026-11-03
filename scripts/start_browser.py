"""Inicia Chromium dedicado. Faça login manualmente na janela aberta."""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--executable',help='Caminho do Chrome/Chromium, se não estiver no PATH.')
    parser.add_argument('--profile',default=str(Path.home()/'.ceab-chromium'))
    parser.add_argument('--port',type=int,default=9222)
    parser.add_argument('--headless',action='store_true',help='Apenas testes locais, sem login interativo.')
    parser.add_argument('--url',default='about:blank')
    args=parser.parse_args()
    candidates=[args.executable,shutil.which('chromium'),shutil.which('google-chrome'),shutil.which('chrome'),'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',os.path.expandvars(r'%PROGRAMFILES%\Google\Chrome\Application\chrome.exe'),os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe')]
    executable=next((x for x in candidates if x and Path(x).is_file()),None)
    if not executable: parser.error('Chrome/Chromium não encontrado. Use --executable com o caminho completo.')
    profile=Path(args.profile).expanduser().resolve()
    profile.mkdir(parents=True,exist_ok=True)
    command=[executable,f'--remote-debugging-port={args.port}','--remote-debugging-address=127.0.0.1',f'--user-data-dir={profile}','--disable-popup-blocking','--no-first-run','--no-default-browser-check']
    if args.headless: command.append('--headless=new')
    command.append(args.url)
    return subprocess.call(command)

if __name__=='__main__': sys.exit(main())
