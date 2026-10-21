"""Profile, avatar and assignment integration in a real browser; disposable data only."""
import sys
import threading
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import expect,sync_playwright
from werkzeug.serving import make_server
from test_app import KanbanTests
from test_profiles import picture


def main():
    fixture=KanbanTests();fixture.setUp();server=None
    output=Path(__file__).resolve().parents[1]/'data'/'previews';output.mkdir(parents=True,exist_ok=True)
    try:
        member=fixture.create_user('member')
        others=[fixture.create_user('person'+str(i)) for i in range(3)]
        task_id=fixture.task()
        ids=[fixture.admin['id'],member['id'],others[0]['id'],others[1]['id']]
        fixture.request('PATCH','/api/tasks/'+task_id+'/assignees',{'version':fixture.board()['version'],'add':ids})
        client,csrf=fixture.login_user('member')
        server=make_server('127.0.0.1',0,fixture.app,threaded=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1000},locale='pt-BR')
            context.add_cookies([{'name':'kanban_session','value':client.get_cookie('kanban_session').value,'url':origin,'httpOnly':True,'sameSite':'Strict'}])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(origin);expect(page.locator('#workspace')).to_be_visible()
            expect(page.locator('.card .user-avatar')).to_have_count(4)
            page.locator('#profile-menu-toggle').click()
            expect(page.locator('#profile-dropdown')).to_be_visible()
            page.locator('#my-profile').click()
            expect(page.locator('#profile-dialog')).to_be_visible()
            expect(page.locator('#profile-username')).to_have_value('member')
            expect(page.locator('#profile-username')).to_be_disabled()
            page.locator('#display-name').fill('Mariana Silva')
            expect(page.locator('#avatar-preview .user-avatar')).to_have_text('MS')
            page.locator('#avatar-file').set_input_files({'name':'foto.png','mimeType':'image/png','buffer':picture()})
            expect(page.locator('#avatar-preview img')).to_be_visible()
            assert page.locator('#avatar-preview img').get_attribute('src').startswith('blob:')
            page.locator('#profile-form .primary').click()
            expect(page.locator('#profile-dialog')).not_to_be_visible()
            expect(page.locator('#profile-name')).to_have_text('Mariana Silva')
            expect(page.locator('#profile-avatar img')).to_be_visible()
            expect(page.locator('.card .user-avatar[title="Mariana Silva"] img')).to_be_visible()
            page.reload();expect(page.locator('#workspace')).to_be_visible()
            expect(page.locator('#profile-name')).to_have_text('Mariana Silva')
            expect(page.locator('#profile-avatar img')).to_be_visible()
            page.get_by_role('button',name='Detalhes / Editar',exact=True).click()
            expect(page.locator('#assigned-people .assigned-person')).to_have_count(4)
            expect(page.locator('#assigned-people')).to_contain_text('Mariana Silva')
            page.locator('#assignee-picker summary').click()
            own=page.locator('#assignee-options input[data-user-id="'+member['id']+'"]')
            expect(own).to_be_enabled()
            expect(page.locator('#assignee-options input[data-user-id="'+fixture.admin['id']+'"]')).to_be_disabled()
            expect(page.locator('#assignee-options .assignee-option').filter(has_text='Mariana Silva').locator('img')).to_be_visible()
            own.uncheck()
            expect(page.locator('#assigned-people .assigned-person')).to_have_count(3)
            own=page.locator('#assignee-options input[data-user-id="'+member['id']+'"]');own.check()
            expect(page.locator('#assigned-people .assigned-person')).to_have_count(4)
            page.locator('#cancel').click()
            fixture.request('PATCH','/api/tasks/'+task_id+'/assignees',{'version':fixture.board()['version'],'add':[others[2]['id']]})
            page.locator('#refresh').click()
            expect(page.locator('.card .user-avatar')).to_have_count(4)
            expect(page.locator('.card .avatar-extra')).to_have_text('+2')
            page.screenshot(path=str(output/'perfis-quadro.png'),full_page=True)
            page.locator('#profile-menu-toggle').click();page.locator('#my-profile').click()
            page.screenshot(path=str(output/'meu-perfil.png'),full_page=True)
            page.locator('#avatar-remove').click()
            expect(page.locator('#avatar-preview .user-avatar')).to_have_text('MS')
            page.locator('#profile-form .primary').click()
            expect(page.locator('#profile-avatar img')).to_have_count(0)
            expect(page.locator('#profile-avatar')).to_have_text('MS')
            page.locator('#profile-menu-toggle').click();page.locator('#my-profile').click()
            page.locator('#profile-change-password').click()
            expect(page.locator('#password-screen')).to_be_visible()
            page.locator('#current-password').fill('changed-password-123')
            page.locator('#new-password').fill('new-browser-password')
            page.locator('#confirm-password').fill('new-browser-password')
            page.locator('#password-form .primary').click()
            expect(page.locator('#workspace')).to_be_visible()
            expect(page.locator('#profile-name')).to_have_text('Mariana Silva')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.locator('#profile-menu-toggle').click();page.locator('#my-profile').click()
            assert page.locator('#profile-dialog').evaluate('e=>e.scrollWidth<=e.clientWidth')
            assert not errors,errors
            browser.close()
        print('PASS: member profile menu, display name/login separation, avatar preview/upload/remove, persistence, real assignment avatars, +N overflow, permission-aware picker, password change and mobile layout')
    finally:
        if server:
            server.shutdown();thread.join(timeout=5);server.server_close()
        fixture.tearDown()


if __name__=='__main__':main()
