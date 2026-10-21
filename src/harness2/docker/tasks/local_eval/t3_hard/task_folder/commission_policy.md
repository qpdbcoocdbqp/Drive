# Sales Commission Policy (effective 2026-01-01)

1. Commission is earned per rep per calendar month on USD-credited bookings.
2. Each deal is converted to USD at the rate for the month of its close_date.
3. Split deals: the split rep is credited split_pct % of the deal; the owner is credited the rest.
   Each credited share counts toward that rep's own monthly volume.
4. Plans are marginal tiers on the rep's cumulative USD credited in the month, applied to deals in
   order of close_date (ties: deal_id ascending):
   - Junior: 5% on the first 20,000 USD of the month, 7% above that.
   - Senior: 6% on the first 30,000 USD of the month, 9% above that.
   A deal that crosses the threshold is split between the two rates.
5. The plan used for a deal is the rep's role in effect on the deal's close_date.
6. Clawback: if a deal is refunded within 60 days of close_date (inclusive), each credited rep
   repays the same fraction of their commission on that deal as the fraction of the deal refunded.
   The clawback is booked in the month of the return_date. Refunds after 60 days have no clawback.
