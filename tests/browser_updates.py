"""Existing UI plus updater settings/permission controls in real Edge."""
import sys
import threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import expect,sync_playwright
from werkzeug.serving import make_server
from test_updates import UpdateTests


def main():
    fixture=UpdateTests();fixture.setUp()
    server=make_server('127.0.0.1',0,fixture.app,threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    raw=fixture.bundle()
    def github(path,token='',binary=False):return raw if binary else [fixture.release(raw)]
    try:
        with patch('updater.github',side_effect=github),sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1000},locale='pt-BR')
            context.add_cookies([{'name':'kanban_session','value':fixture.client.get_cookie('kanban_session').value,'url':origin,'httpOnly':True,'sameSite':'Strict'}])
            page=context.new_page();errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(origin);page.wait_for_function('() => user && !pending');page.locator('#configuration-menu').click()
            expect(page.locator('#updates-panel')).to_be_visible()
            expect(page.locator('#admin')).to_be_hidden()
            expect(page.locator('#update-status')).to_contain_text('1.0.0')
            page.locator('#update-settings summary').click()
            page.locator('#update-owner').fill('presotto');page.locator('#update-repository').fill('kanban')
            page.locator('#update-settings-form button').click()
            expect(page.locator('#notice')).to_contain_text('Repositório salvo')
            page.locator('#update-check').click()
            expect(page.locator('#update-status')).to_contain_text('Atualização disponível')
            expect(page.locator('#update-notes')).to_contain_text('<script>')
            assert page.locator('#update-notes script').count()==0
            page.locator('#update-download').click()
            expect(page.locator('#update-progress-label')).to_contain_text('Pacote validado')
            expect(page.locator('#update-install')).to_be_disabled()
            page.locator('#update-history-button').click()
            expect(page.locator('#update-history')).to_contain_text('Nenhuma instalação')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            output=Path('data/previews');output.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(output/'atualizacoes-mobile.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':1000})
            fixture.create_user(role='Member',overrides={'view_update_status':True})
            member,csrf=fixture.login_user()
            context.clear_cookies();context.add_cookies([{'name':'kanban_session','value':member.get_cookie('kanban_session').value,'url':origin,'httpOnly':True,'sameSite':'Strict'}])
            page.reload();page.wait_for_function('() => user && !pending');page.locator('#configuration-menu').click()
            expect(page.locator('#updates-panel')).to_be_visible()
            expect(page.locator('#settings')).to_be_hidden()
            expect(page.locator('#appearance')).to_be_hidden()
            expect(page.locator('#update-check')).to_be_hidden()
            expect(page.locator('#update-settings')).to_be_hidden()
            expect(page.locator('#update-install')).to_be_hidden()
            assert not errors,errors
            browser.close()
        print('PASS: atualizações, configuração, consulta, notas seguras, download, permissões e celular')
    finally:
        server.shutdown();thread.join(timeout=5);server.server_close();fixture.tearDown()


if __name__=='__main__':main()
