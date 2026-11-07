# This file is part of the Tryton restaurant module.
from trytond.i18n import gettext
from trytond.model import ModelView, fields
from trytond.wizard import Button, StateView, Wizard


class GuideStart(ModelView):
    "Usage Guide"
    __name__ = 'restaurant.guide.start'

    content = fields.Text("Guide", readonly=True,
        help="Text of the usage guide.")


class Guide(Wizard):
    "Usage Guide"
    __name__ = 'restaurant.guide'

    start = StateView(
        'restaurant.guide.start', 'restaurant.guide_start_view_form', [
            Button("Close", 'end', 'tryton-close', default=True),
            ])

    def default_start(self, fields):
        return {'content': gettext('restaurant.msg_guide_usage')}
