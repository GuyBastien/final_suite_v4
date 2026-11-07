# This file is part of the Tryton party_person_ht module.
import re

from trytond.i18n import gettext
from trytond.model import fields
from trytond.pool import PoolMeta
from trytond.pyson import Eval

from .exceptions import InvalidHaitiIdentifier

HT_IDENTIFIER_TYPES = [
    ('ht_nif', "Haitian Tax Identification Number (NIF)"),
    ('ht_ninu', "Haitian Unique National Identification Number (NINU)"),
    ]
_NIF = re.compile(r'^\d{10}$')
_NINU = re.compile(r'^\d{10,17}$')


def compact(code):
    "Remove the separators of an identifier code"
    return re.sub(r'[\s\-\.]', '', code or '')


def format_nif(code):
    "Display a NIF as 000-000-000-0"
    code = compact(code)
    if _NIF.match(code):
        return '-'.join([code[:3], code[3:6], code[6:9], code[9:]])
    return code


def is_valid_nif(code):
    return bool(_NIF.match(compact(code)))


def is_valid_ninu(code):
    return bool(_NINU.match(compact(code)))


class Party(metaclass=PoolMeta):
    __name__ = 'party.party'

    is_person = fields.Boolean(
        "Person",
        help="Check if the party is a natural person: the first name "
        "is then available.")
    first_name = fields.Char(
        "First Name", strip=True,
        states={'invisible': ~Eval('is_person', False)},
        help="First name of the natural person. The field Name "
        "holds the family name.")

    @classmethod
    def default_is_person(cls):
        return False

    @classmethod
    def tax_identifier_types(cls):
        return super().tax_identifier_types() + ['ht_nif']

    def get_full_name(self, name):
        full_name = super().get_full_name(name)
        if self.is_person and self.first_name and full_name:
            return '%s %s' % (self.first_name, full_name)
        return full_name

    def get_rec_name(self, name):
        rec_name = super().get_rec_name(name)
        if self.is_person and self.first_name and self.name:
            return '%s %s' % (self.first_name, rec_name)
        return rec_name

    @classmethod
    def search_rec_name(cls, name, clause):
        domain = super().search_rec_name(name, clause)
        _, operator, operand, *extra = clause
        bool_op = 'AND' if (
            operator.startswith('!') or operator.startswith('not ')) else 'OR'
        return [bool_op, domain, ('first_name', operator, operand, *extra)]

    @fields.depends('is_person', 'first_name')
    def on_change_is_person(self):
        if not self.is_person:
            self.first_name = None


class Configuration(metaclass=PoolMeta):
    __name__ = 'party.configuration'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        known = {k for k, _ in cls.identifier_types.selection}
        for item in HT_IDENTIFIER_TYPES:
            if item[0] not in known:
                cls.identifier_types.selection.append(item)


class Identifier(metaclass=PoolMeta):
    __name__ = 'party.identifier'

    @fields.depends('type', 'code')
    def on_change_with_code(self):
        code = super().on_change_with_code()
        if self.type == 'ht_nif' and is_valid_nif(code):
            return compact(code)
        if self.type == 'ht_ninu' and is_valid_ninu(code):
            return compact(code)
        return code

    @fields.depends('type', 'party', 'code')
    def check_code(self):
        super().check_code()
        checks = {'ht_nif': is_valid_nif, 'ht_ninu': is_valid_ninu}
        if self.type in checks and not checks[self.type](self.code):
            party = self.party.rec_name if (
                self.party and self.party.id and self.party.id > 0) else ''
            raise InvalidHaitiIdentifier(gettext(
                    'party_person_ht.msg_invalid_code_%s' % self.type[3:],
                    code=self.code, party=party))
