import unittest
from bundle import Server

class ServerTestCase(unittest.TestCase):

    def test_dummy(self):
        server = Server(dict(name="truc", url='http'))
        self.assertEquals(server.name, 'truc')

def test_suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(ServerTestCase))
    return suite
