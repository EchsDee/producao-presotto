import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server
from test_app import KanbanTests


def main():
    fixture = KanbanTests()
    fixture.setUp()
    server = None
    try:
        task_id = fixture.task()
        fixture.upload(task_id, 'admin.txt', b'admin attachment')
        fixture.request('PATCH', '/api/tasks/' + task_id + '/assignees', {
            'version': fixture.board()['version'], 'add': [fixture.admin['id']]})
        member = fixture.create_user('member', 'Member')
        fixture.create_user('upload-only', 'Viewer', {'upload_attachments': True})
        self_only = fixture.create_user('self-only', 'Viewer', {'assign_self': True})
        fixture.create_user('blind-manager', 'Manager', {'view_task_assignees': False})
        clients = {name: fixture.login_user(name)[0] for name in ['member', 'upload-only', 'self-only', 'blind-manager']}
        server = make_server('127.0.0.1', 0, fixture.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            errors = []
            def page_for(name):
                context = browser.new_context()
                context.add_cookies([{'name': 'kanban_session', 'value': clients[name].get_cookie('kanban_session').value,
                    'url': origin, 'httpOnly': True, 'sameSite': 'Strict'}])
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin)
                expect(page.locator('#workspace')).to_be_visible()
                return page

            page = page_for('member')
            page.locator('.task-title').click()
            expect(page.locator('#assignees option[value="' + fixture.admin['id'] + '"]')).to_be_disabled()
            expect(page.locator('#assignees option[value="' + member['id'] + '"]')).to_be_enabled()
            with page.expect_response(lambda r: r.url.endswith('/assignees') and r.request.method == 'PATCH') as result:
                page.locator('#assignees').evaluate("(select,id)=>{select.querySelector('[value=\"'+id+'\"]').selected=true;select.dispatchEvent(new Event('change',{bubbles:true}))}", member['id'])
            assert result.value.status == 200
            expect(page.locator('#task-history')).to_contain_text('atribuiu o usuário “member”')
            page.locator('#files').set_input_files({'name': 'member.txt', 'mimeType': 'text/plain', 'buffer': b'own file'})
            expect(page.get_by_role('link', name='member.txt')).to_be_visible()
            expect(page.locator('.attachment').filter(has_text='admin.txt').get_by_role('button', name='Excluir arquivo')).to_have_count(0)
            own_file = page.locator('.attachment').filter(has_text='member.txt')
            expect(own_file.get_by_role('button', name='Excluir arquivo')).to_be_visible()
            page.once('dialog', lambda dialog: dialog.accept())
            own_file.get_by_role('button', name='Excluir arquivo').click()
            expect(page.get_by_role('link', name='member.txt')).to_have_count(0)
            expect(page.locator('#task-history')).to_contain_text('excluiu o arquivo “member.txt”')
            page.locator('#cancel').click()
            page.locator('#my-tasks').click()
            expect(page.locator('.card')).to_have_count(1)
            page.locator('#assignee-filter').select_option(label='upload-only')
            expect(page.locator('.card')).to_have_count(0)
            page.context.close()

            page = page_for('upload-only')
            page.locator('.task-title').click()
            expect(page.locator('#title')).to_be_disabled()
            expect(page.locator('#upload-form')).to_be_visible()
            page.locator('#files').set_input_files({'name': 'upload-only.txt', 'mimeType': 'text/plain', 'buffer': b'no delete right'})
            expect(page.get_by_role('link', name='upload-only.txt')).to_be_visible()
            expect(page.get_by_role('button', name='Excluir arquivo')).to_have_count(0)
            page.context.close()

            page = page_for('self-only')
            page.locator('.task-title').click()
            expect(page.locator('#title')).to_be_disabled()
            expect(page.locator('#save-task')).to_be_hidden()
            with page.expect_response(lambda r: r.url.endswith('/assignees') and r.request.method == 'PATCH') as result:
                page.locator('#assignees').evaluate("(select,id)=>{select.querySelector('[value=\"'+id+'\"]').selected=true;select.dispatchEvent(new Event('change',{bubbles:true}))}", self_only['id'])
            assert result.value.status == 200
            expect(page.locator('#task-history')).to_contain_text('atribuiu o usuário “self-only”')
            page.locator('#cancel').click()
            page.locator('#my-tasks').click()
            expect(page.locator('.card')).to_have_count(1)
            page.context.close()

            page = page_for('blind-manager')
            expect(page.locator('.card .assignees')).to_have_count(0)
            page.locator('.task-title').click()
            expect(page.locator('#assignee-section')).to_be_hidden()
            expect(page.locator('#assignment-controls')).to_be_visible()
            page.locator('#assignment-person').select_option(label='upload-only')
            page.locator('#assignment-add').click()
            expect(page.locator('#notice')).to_have_text('Alterações salvas.')

            assert page.evaluate('board.tasks[0].assignees.length') == 0
            page.locator('#assignment-remove').click()
            expect(page.locator('#task-history')).not_to_contain_text('atribuiu o usuário “upload-only”')
            assert not errors, errors
            browser.close()
        print('PASS: member own-file deletion, upload without deletion, self-assignment without task editing, assignment filters, independent assignee visibility')
    finally:
        if server:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        fixture.tearDown()


if __name__ == '__main__':
    main()
