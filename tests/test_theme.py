import unittest
import test_app
from app import create_app


class ThemeTests(unittest.TestCase):
    setUp = test_app.KanbanTests.setUp
    tearDown = test_app.KanbanTests.tearDown
    request = test_app.KanbanTests.request
    create_user = test_app.KanbanTests.create_user
    login_user = test_app.KanbanTests.login_user

    def test_theme_persistence_conflicts_validation_and_public_css(self):
        colors = {'navy':'#121234','yellow':'#008800','canvas':'#ededed'}
        response = self.request('PATCH','/api/theme',{'version':0,'colors':colors})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.request('PATCH','/api/theme',{'version':0,'colors':colors}).status_code,409)
        self.assertEqual(self.request('PATCH','/api/theme',{'version':1,'colors':{**colors,'navy':'red;bad'}}).status_code,400)
        restarted = create_app(self.config)
        css = restarted.test_client().get('/theme.css').get_data(as_text=True)
        self.assertIn('--yellow:#008800;',css)
        self.assertEqual(self.client.get('/api/theme').json['theme']['version'],1)
        entries = self.client.get('/api/activity?action=settings.theme_changed').json['entries']
        self.assertEqual(len(entries),1)
        self.assertEqual(entries[0]['new'],colors)

    def test_delegated_user_manager_cannot_change_theme(self):
        self.create_user(role='Manager',overrides={'manage_users':True,'manage_permissions':True})
        client,csrf = self.login_user()
        result = self.request('PATCH','/api/theme',{'version':0,'colors':{'navy':'#111111','yellow':'#eeeeee','canvas':'#ffffff'}},client,csrf)
        self.assertEqual(result.status_code,403)
        self.assertEqual(self.client.get('/api/theme').json['theme']['version'],0)
