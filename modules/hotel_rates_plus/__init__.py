# This file is part of the Tryton hotel_rates_plus module.
from trytond.pool import Pool

from . import kpi, rate_plus, reservation


def register():
    Pool.register(
        rate_plus.RateRestriction,
        rate_plus.MealPlan,
        rate_plus.RateSupplement,
        rate_plus.RateContract,
        rate_plus.PricingRule,
        reservation.Hotel,
        reservation.Reservation,
        reservation.ReservationLine,
        reservation.ReservationCharge,
        kpi.RevenueKPI,
        module='hotel_rates_plus', type_='model')
