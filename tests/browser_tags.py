import sys
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server
from test_app import KanbanTests


def main():
    fixture = KanbanTests(); fixture.setUp()
    fixture.create_user()
    member, token = fixture.login_user()
    server = make_server('127.0.0.1', 0, fixture.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(locale='pt-BR', viewport={'width': 1440, 'height': 1000})
            context.add_cookies([{'name': 'kanban_session', 'value': fixture.client.get_cookie('kanban_session').value, 'url': origin}])
            page = context.new_page(); errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(origin); expect(page.locator('#workspace')).to_be_visible()
            page.locator('#settings').click(); page.locator('#admin-tags-tab').click()
            for name, color in [('Corte', '#ffcc00'), ('Laser', '#000080')]:
                page.locator('#new-tag').click(); page.locator('#tag-name').fill(name)
                page.locator('#tag-color').fill(color); page.locator('#tag-description').fill('Produção')
                page.locator('#tag-form .primary').click()
                expect(page.locator('#tag-dialog')).not_to_be_visible()
            expect(page.locator('#tag-list tr')).to_have_count(2)
            page.locator('#tag-search').fill('laser'); expect(page.locator('#tag-list tr')).to_have_count(1)
            page.locator('#tag-search').fill('')
            page.locator('#back-board').click(); page.locator('#new').click()
            page.locator('#title').fill('Peça A')
            expect(page.locator('#task-tag-options')).not_to_be_visible()
            page.locator('#task-tag-search').click()
            expect(page.locator('#task-tag-options .tag-choice')).to_have_count(2)
            expect(page.locator('#task-tag-options')).to_be_visible()
            page.locator('#title').click()
            expect(page.locator('#task-tag-options')).not_to_be_visible()
            page.locator('#task-tag-search').click()
            page.locator('#task-tag-search').press('Escape')
            expect(page.locator('#task-tag-options')).not_to_be_visible()
            expect(page.locator('#dialog')).to_be_visible()
            page.locator('#task-tag-search').fill('cort')
            page.locator('#task-tag-options .tag-choice').filter(has_text='Corte').click()
            expect(page.locator('#task-tag-options')).to_be_visible()
            expect(page.locator('#task-tag-options input[type=checkbox]')).to_have_count(0)
            expect(page.locator('#task-selected-tags')).to_contain_text('Corte')
            page.locator('#task-tag-options .tag-choice').filter(has_text='Laser').click()
            expect(page.locator('#task-selected-tags .task-tag')).to_have_count(2)
            expect(page.locator('#task-tag-options')).to_be_visible()
            page.locator('#save-task').click(); expect(page.locator('#form-title')).to_have_text('Detalhes da tarefa')
            page.locator('#cancel').click()
            expect(page.locator('.card .task-tag')).to_have_count(2)
            expect(page.locator('.card .task-tag').filter(has_text='Laser')).to_have_css('background-color', 'rgb(0, 0, 128)')
            page.locator('#new').click(); page.locator('#title').fill('Peça B'); page.locator('#save-task').click()
            expect(page.locator('#form-title')).to_have_text('Detalhes da tarefa'); page.locator('#cancel').click()
            page.locator('#tag-filter-panel summary').click()
            page.locator('#tag-filters label').filter(has_text='Corte').locator('input').check()
            expect(page.locator('.card')).to_have_count(1)
            page.locator('#tag-filters label').filter(has_text='Laser').locator('input').check()
            expect(page.locator('.card')).to_have_count(1)
            page.locator('#clear-tag-filters').click(); expect(page.locator('.card')).to_have_count(2)
            page.locator('#search').fill('laser'); expect(page.locator('.card')).to_have_count(1)
            page.locator('#search').fill('')
            page.locator('#settings').click(); page.locator('#admin-tags-tab').click()
            row = page.locator('#tag-list tr').filter(has_text='Corte')
            row.get_by_role('button', name='Editar').click(); page.locator('#tag-name').fill('Corte CNC')
            page.locator('#tag-active').uncheck(); page.locator('#tag-form .primary').click()
            expect(page.locator('#tag-dialog')).not_to_be_visible(); page.locator('#back-board').click()
            expect(page.locator('.card .task-tag').filter(has_text='Corte CNC')).to_have_count(1)
            page.locator('#new').click(); expect(page.locator('#task-tag-options')).not_to_contain_text('Corte CNC')
            page.locator('#cancel').click()
            member_context = browser.new_context(locale='pt-BR')
            member_context.add_cookies([{'name': 'kanban_session', 'value': member.get_cookie('kanban_session').value, 'url': origin}])
            regular = member_context.new_page(); regular.on('pageerror', lambda e: errors.append(str(e)))
            regular.goto(origin); expect(regular.locator('#workspace')).to_be_visible()
            expect(regular.locator('#settings')).not_to_be_visible()
            regular.locator('.card').filter(has_text='Peça B').locator('.task-title').click()
            regular.locator('#task-tag-search').fill('las')
            regular.locator('#task-tag-options .tag-choice').filter(has_text='Laser').click()
            expect(regular.locator('#task-history')).to_contain_text('Laser')
            regular.wait_for_function('() => !pending')
            expect(regular.locator('#task-tag-options')).to_be_visible()
            regular.locator('#cancel').click()
            expect(regular.locator('.card').filter(has_text='Peça B').locator('.task-tag')).to_have_text('Laser')
            regular.reload(); expect(regular.locator('.card').filter(has_text='Peça B').locator('.task-tag')).to_have_text('Laser')
            page.locator('#settings').click(); page.locator('#admin-tags-tab').click()
            deletion = []
            page.on('dialog', lambda dialog: (deletion.append(dialog.message), dialog.accept()))
            page.locator('#tag-list tr').filter(has_text='Laser').get_by_role('button', name='Excluir').click()
            expect(page.locator('#tag-list')).not_to_contain_text('Laser')
            assert deletion and '2 tarefa(s)' in deletion[0]
            page.locator('#back-board').click(); expect(page.locator('.card')).to_have_count(2)
            expect(page.locator('.card .task-tag')).to_have_count(1)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors, errors
            browser.close()
        print('PASS: tag management, colors, selection, filters, inactive retention, member assignments, deletion and mobile layout')
    finally:
        server.shutdown(); server.server_close(); fixture.tearDown()


if __name__ == '__main__':
    main()
