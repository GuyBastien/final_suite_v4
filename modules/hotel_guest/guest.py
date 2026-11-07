# This file is part of the Tryton hotel_guest module.
from decimal import Decimal

from trytond.i18n import gettext
from trytond.model import ModelSQL, ModelView, Unique, Workflow, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval, If
from trytond.transaction import Transaction

from .exceptions import GuestError

ID_TYPES = [
    (None, ""),
    ('passport', "Passport"),
    ('id_card', "ID Card"),
    ('driving_license', "Driving License"),
    ('other', "Other"),
    ]
TIERS = [
    (None, ""),
    ('silver', "Silver"),
    ('gold', "Gold"),
    ]
OPEN_STATES = ['draft', 'confirmed', 'checked_in']


class Party(metaclass=PoolMeta):
    __name__ = 'party.party'

    nationality = fields.Many2One('country.country', "Nationality")
    birth_date = fields.Date("Birth Date")
    id_type = fields.Selection(ID_TYPES, "ID Document Type")
    id_number = fields.Char("ID Document Number")
    id_expiry = fields.Date("ID Document Expiry")
    vip = fields.Boolean("VIP")
    preferences = fields.Text(
        "Preferences", help="Room, pillow, diet and other preferences.")
    blacklisted = fields.Boolean(
        "Blacklisted", help="A blacklisted guest cannot be confirmed in a "
        "reservation.")
    blacklist_reason = fields.Text(
        "Blacklist Reason", states={'invisible': ~Eval('blacklisted', False)})
    gdpr_consent = fields.Boolean("Data Processing Consent")
    consent_date = fields.Date(
        "Consent Date", states={'invisible': ~Eval('gdpr_consent', False)})
    marketing_consent = fields.Boolean("Marketing Consent")
    anonymized = fields.Boolean("Anonymized", readonly=True)
    stays_count = fields.Function(
        fields.Integer("Stays"), 'get_stays_count')
    loyalty_points = fields.Function(
        fields.Integer("Loyalty Points"), 'get_loyalty')
    loyalty_tier = fields.Function(
        fields.Selection(TIERS, "Loyalty Tier"), 'get_loyalty')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'anonymize': {
                    'invisible': Eval('anonymized', False),
                    'depends': ['anonymized'],
                    },
                })

    @classmethod
    def copy(cls, parties, default=None):
        default = {} if default is None else default.copy()
        for name in ['id_type', 'id_number', 'id_expiry', 'birth_date',
                'blacklisted', 'blacklist_reason', 'gdpr_consent',
                'consent_date', 'anonymized']:
            default.setdefault(name, None)
        return super().copy(parties, default=default)

    @classmethod
    def get_stays_count(cls, parties, name):
        Reservation = Pool().get('hotel.reservation')
        return {p.id: Reservation.search_count([
                    ('party', '=', p.id), ('state', '=', 'checked_out')])
            for p in parties}

    @classmethod
    def get_loyalty(cls, parties, names):
        pool = Pool()
        Entry = pool.get('hotel.loyalty.entry')
        Program = pool.get('hotel.loyalty.program')
        company = Transaction().context.get('company')
        program = Program.get_program(company)
        result = {n: {} for n in names}
        for party in parties:
            points = sum(
                e.points for e in Entry.search([('party', '=', party.id)]))
            tier = None
            if program:
                if points >= program.gold_threshold:
                    tier = 'gold'
                elif points >= program.silver_threshold:
                    tier = 'silver'
            if 'loyalty_points' in result:
                result['loyalty_points'][party.id] = points
            if 'loyalty_tier' in result:
                result['loyalty_tier'][party.id] = tier
        return result

    @classmethod
    @ModelView.button
    def anonymize(cls, parties):
        "GDPR: erase the personal data of the guests"
        pool = Pool()
        Reservation = pool.get('hotel.reservation')
        Registration = pool.get('hotel.guest.registration')
        Mechanism = pool.get('party.contact_mechanism')
        Address = pool.get('party.address')
        for party in parties:
            if Reservation.search([
                        ('party', '=', party.id),
                        ('state', 'in', OPEN_STATES)], limit=1):
                raise GuestError(gettext(
                        'hotel_guest.msg_guest_anonymize_open',
                        party=party.rec_name))
        for party in parties:
            registrations = Registration.search([
                    ('reservation.party', '=', party.id)])
            Registration.write(registrations, {
                    'first_name': None, 'name': gettext(
                        'hotel_guest.msg_guest_anonymized_name'),
                    'nationality': None, 'birth_date': None,
                    'id_type': None, 'id_number': None})
            Mechanism.delete(list(party.contact_mechanisms))
            Address.write(list(party.addresses), {'active': False})
        cls.write(parties, {
                'name': gettext('hotel_guest.msg_guest_anonymized_name'),
                'first_name': None, 'nationality': None, 'birth_date': None,
                'id_type': None, 'id_number': None, 'id_expiry': None,
                'preferences': None, 'vip': False, 'blacklist_reason': None,
                'gdpr_consent': False, 'consent_date': None,
                'marketing_consent': False, 'anonymized': True,
                'active': False})


class Hotel(metaclass=PoolMeta):
    __name__ = 'hotel.hotel'

    require_registration = fields.Boolean(
        "Require Guest Registration",
        help="Require a registration card for every guest (adults and "
        "children) at check-in.")

    @classmethod
    def default_require_registration(cls):
        return False


class LoyaltyProgram(ModelSQL, ModelView):
    "Loyalty Program"
    __name__ = 'hotel.loyalty.program'

    company = fields.Many2One('company.company', "Company", required=True)
    points_per_currency = fields.Numeric(
        "Points per Currency Unit", digits=(16, 4), required=True,
        domain=[('points_per_currency', '>=', 0)],
        help="Points earned for each currency unit of the stay.")
    silver_threshold = fields.Integer(
        "Silver Threshold", required=True,
        domain=[('silver_threshold', '>=', 0)])
    gold_threshold = fields.Integer(
        "Gold Threshold", required=True,
        domain=[('gold_threshold', '>=', Eval('silver_threshold', 0))])

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('company_unique', Unique(t, t.company),
                'hotel_guest.msg_loyalty_program_unique'),
            ]

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_points_per_currency(cls):
        return Decimal(1)

    @classmethod
    def default_silver_threshold(cls):
        return 1000

    @classmethod
    def default_gold_threshold(cls):
        return 5000

    @classmethod
    def get_program(cls, company):
        if company:
            programs = cls.search([('company', '=', company)], limit=1)
            if programs:
                return programs[0]


class LoyaltyEntry(ModelSQL, ModelView):
    "Loyalty Entry"
    __name__ = 'hotel.loyalty.entry'

    company = fields.Many2One('company.company', "Company", required=True)
    party = fields.Many2One(
        'party.party', "Guest", required=True,
        context={'company': Eval('company', -1)}, depends=['company'])
    date = fields.Date("Date", required=True)
    kind = fields.Selection([
            ('earn', "Earned"),
            ('redeem', "Redeemed"),
            ('adjust', "Adjustment"),
            ], "Kind", required=True)
    points = fields.Integer("Points", required=True)
    reason = fields.Char("Reason")
    reservation = fields.Many2One(
        'hotel.reservation', "Reservation", readonly=True,
        ondelete='SET NULL')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order.insert(0, ('date', 'DESC'))
        cls._order.insert(1, ('id', 'DESC'))

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_date(cls):
        return Pool().get('ir.date').today()

    @classmethod
    def default_kind(cls):
        return 'adjust'

    @classmethod
    def earn(cls, reservation):
        "Credit the points of a reservation, once"
        Program = Pool().get('hotel.loyalty.program')
        program = Program.get_program(reservation.company.id)
        if program is None:
            return
        if cls.search([
                    ('reservation', '=', reservation.id),
                    ('kind', '=', 'earn')], limit=1):
            return
        points = int(
            (reservation.total_amount or Decimal(0))
            * program.points_per_currency)
        if points <= 0:
            return
        entry, = cls.create([{
                    'company': reservation.company.id,
                    'party': reservation.party.id,
                    'kind': 'earn', 'points': points,
                    'reason': reservation.rec_name,
                    'reservation': reservation.id}])
        return entry


class GuestRegistration(ModelSQL, ModelView):
    "Guest Registration"
    __name__ = 'hotel.guest.registration'

    reservation = fields.Many2One(
        'hotel.reservation', "Reservation", required=True,
        ondelete='CASCADE')
    company = fields.Function(
        fields.Many2One('company.company', "Company"),
        'get_company', searcher='search_company')
    first_name = fields.Char("First Name")
    name = fields.Char("Name", required=True)
    nationality = fields.Many2One('country.country', "Nationality")
    birth_date = fields.Date("Birth Date")
    id_type = fields.Selection(ID_TYPES, "ID Document Type")
    id_number = fields.Char("ID Document Number")

    def get_company(self, name):
        return self.reservation.company.id

    @classmethod
    def search_company(cls, name, clause):
        return [('reservation.company',) + tuple(clause[1:])]


class Keycard(Workflow, ModelSQL, ModelView):
    "Key Card"
    __name__ = 'hotel.keycard'
    _rec_name = 'code'

    reservation = fields.Many2One(
        'hotel.reservation', "Reservation", required=True,
        ondelete='CASCADE', states={'readonly': Eval('state') != 'draft'})
    company = fields.Function(
        fields.Many2One('company.company', "Company"),
        'get_company', searcher='search_company')
    room = fields.Many2One(
        'hotel.room', "Room", required=True,
        states={'readonly': Eval('state') != 'draft'})
    code = fields.Char(
        "Code", required=True, states={'readonly': Eval('state') != 'draft'})
    valid_from = fields.DateTime("Valid From")
    valid_to = fields.DateTime("Valid To")
    valid_to_date = fields.Function(fields.Date("Valid Until"),
        'get_valid_to_date')
    state = fields.Selection([
            ('draft', "Draft"),
            ('issued', "Issued"),
            ('revoked', "Revoked"),
            ('returned', "Returned"),
            ], "State", readonly=True, required=True, sort=False)

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/tree', 'visual',
                If(Eval('state') == 'draft', 'warning',
                        If(Eval('state') == 'issued', 'success',
                        If(Eval('state') == 'revoked', 'danger',
                        If(Eval('state') == 'returned', 'muted',
                        ''))))),
            ]

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._transitions |= {
            ('draft', 'issued'),
            ('issued', 'revoked'),
            ('issued', 'returned'),
            }
        cls._buttons.update({
                'issue': {
                    'invisible': Eval('state') != 'draft',
                    'depends': ['state'],
                    },
                'revoke': {
                    'invisible': Eval('state') != 'issued',
                    'depends': ['state'],
                    },
                'give_back': {
                    'invisible': Eval('state') != 'issued',
                    'depends': ['state'],
                    },
                })

    @classmethod
    def default_state(cls):
        return 'draft'

    def get_company(self, name):
        return self.reservation.company.id

    @classmethod
    def search_company(cls, name, clause):
        return [('reservation.company',) + tuple(clause[1:])]

    def get_valid_to_date(self, name):
        if self.valid_to:
            return self.valid_to.date()

    @fields.depends(
        'reservation', 'valid_from', 'valid_to', 'room',
        '_parent_reservation.start', '_parent_reservation.end',
        '_parent_reservation.lines')
    def on_change_reservation(self):
        if self.reservation:
            self.valid_from = self.reservation.start
            self.valid_to = self.reservation.end
            lines = [l for l in self.reservation.lines if l.room]
            if len(lines) == 1:
                self.room = lines[0].room

    # Hooks for a door lock vendor integration
    def _provider_issue(self):
        "Encode the card on the lock system (to be extended)"

    def _provider_revoke(self):
        "Invalidate the card on the lock system (to be extended)"

    @classmethod
    @ModelView.button
    @Workflow.transition('issued')
    def issue(cls, cards):
        for card in cards:
            card._provider_issue()

    @classmethod
    @ModelView.button
    @Workflow.transition('revoked')
    def revoke(cls, cards):
        for card in cards:
            card._provider_revoke()

    @classmethod
    @ModelView.button
    @Workflow.transition('returned')
    def give_back(cls, cards):
        for card in cards:
            card._provider_revoke()


class Reservation(metaclass=PoolMeta):
    __name__ = 'hotel.reservation'

    registrations = fields.One2Many(
        'hotel.guest.registration', 'reservation', "Guest Registrations")
    keycards = fields.One2Many('hotel.keycard', 'reservation', "Key Cards")

    @classmethod
    def copy(cls, reservations, default=None):
        default = {} if default is None else default.copy()
        default.setdefault('registrations', None)
        default.setdefault('keycards', None)
        return super().copy(reservations, default=default)

    @classmethod
    @ModelView.button
    @Workflow.transition('confirmed')
    def confirm(cls, reservations):
        for reservation in reservations:
            if reservation.party.blacklisted:
                raise GuestError(gettext(
                        'hotel_guest.msg_guest_blacklisted',
                        reservation=reservation.rec_name,
                        party=reservation.party.rec_name))
        super().confirm(reservations)

    @classmethod
    @ModelView.button
    @Workflow.transition('checked_in')
    def check_in(cls, reservations):
        for reservation in reservations:
            if reservation.hotel.require_registration:
                needed = reservation.adults + reservation.children
                if len(reservation.registrations) < needed:
                    raise GuestError(gettext(
                            'hotel_guest.msg_guest_registration_missing',
                            reservation=reservation.rec_name,
                            needed=needed,
                            count=len(reservation.registrations)))
        super().check_in(reservations)

    @classmethod
    @ModelView.button
    @Workflow.transition('checked_out')
    def check_out(cls, reservations):
        pool = Pool()
        Entry = pool.get('hotel.loyalty.entry')
        Keycard = pool.get('hotel.keycard')
        super().check_out(reservations)
        for reservation in reservations:
            Entry.earn(reservation)
        cards = Keycard.search([
                ('reservation', 'in', [r.id for r in reservations]),
                ('state', '=', 'issued')])
        if cards:
            Keycard.revoke(cards)
