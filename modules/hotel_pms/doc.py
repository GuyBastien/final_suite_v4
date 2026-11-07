# This file is part of the Tryton hotel_pms module.
from trytond.model import ModelSQL, ModelView, fields, sequence_ordered


class Documentation(sequence_ordered(), ModelSQL, ModelView):
    "Hotel Documentation"
    __name__ = 'hotel.documentation'
    _rec_name = 'title'

    title = fields.Char("Title", required=True, translate=True)
    content = fields.Text("Content", translate=True)
