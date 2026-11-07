# This file is part of the Tryton sale_point_quick module.
import unittest

from trytond.tests import test_tryton


class QuickSaleTestCase(test_tryton.ModuleTestCase):
    "Test sale_point_quick module"
    module = 'sale_point_quick'


def suite():
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            QuickSaleTestCase))
    return suite
