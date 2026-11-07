# This file is part of the Tryton party_person_ht module.
from trytond.pool import PoolMeta

HT_SUBDIVISION_TYPES = [
    ('arrondissement', "Arrondissement"),
    ('communal section', "Communal Section"),
    ]


class Subdivision(metaclass=PoolMeta):
    __name__ = 'country.subdivision'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        known = {k for k, _ in cls.type.selection}
        for item in HT_SUBDIVISION_TYPES:
            if item[0] not in known:
                cls.type.selection.append(item)
