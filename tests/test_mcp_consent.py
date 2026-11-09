import unittest
from gateway.mcp.mcp_consent import choices

class ConsentTests(unittest.TestCase):
    def test_empty_and_boolean_forms(self):
        p={'serverName':'canva','mode':'form','requestedSchema':{'type':'object','properties':{}}}
        self.assertEqual(choices(p)[0][1],{'action':'accept','content':{}})
        p['requestedSchema']['properties']={'confirm':{'type':'boolean'}}
        self.assertEqual(choices(p)[0][1]['content'],{'confirm':True})

    def test_arbitrary_data_and_login_forms_not_sent_to_model(self):
        for mode,schema in [('url',{}),('form',{'type':'object','properties':{'password':{'type':'string'}}})]:
            with self.assertRaises(ValueError):choices({'serverName':'canva','mode':mode,'requestedSchema':schema})
