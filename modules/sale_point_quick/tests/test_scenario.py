# This file is part of the Tryton sale_point_quick module.
from trytond.tests.test_tryton import load_doc_tests


def load_tests(*args, **kwargs):
    return load_doc_tests(__name__, __file__, *args, **kwargs)
