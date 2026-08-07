-- Atomic customer booking cancellation.
-- Prerequisites: crud_hardening_migration.sql and
-- enquiry_refund_logo_migration.sql (for redemption reversal/refunds).

create or replace function public.cancel_booking_atomic(
  p_company_id int,
  p_booking_type text,
  p_booking_id int
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_type text := lower(btrim(p_booking_type));
  v_booking jsonb;
  v_booking_status text;
  v_payment payment%rowtype;
  v_redemption redemption%rowtype;
begin
  -- Lock the target booking first. All later writes happen in this same
  -- transaction, so a refund/points failure also rolls the cancellation back.
  if v_type = 'grooming' then
    select to_jsonb(b), b.booking_status
      into v_booking, v_booking_status
    from grooming_booking b
    where b.company_id = p_company_id
      and b.grooming_booking_id = p_booking_id
    for update;
  elsif v_type = 'daycare' then
    select to_jsonb(b), b.booking_status
      into v_booking, v_booking_status
    from daycare_booking b
    where b.company_id = p_company_id
      and b.daycare_booking_id = p_booking_id
    for update;
  elsif v_type = 'boarding' then
    select to_jsonb(b), b.booking_status
      into v_booking, v_booking_status
    from boarding_booking b
    where b.company_id = p_company_id
      and b.boarding_booking_id = p_booking_id
    for update;
  else
    raise exception 'Unsupported booking type: %', p_booking_type using errcode = 'P0001';
  end if;

  if v_booking is null then
    raise exception 'Booking not found' using errcode = 'P0002';
  end if;
  if lower(coalesce(v_booking_status, '')) = 'cancelled' then
    return v_booking;
  end if;
  if lower(coalesce(v_booking_status, '')) not in ('pending', 'scheduled') then
    raise exception 'Only a Pending or Scheduled booking can be cancelled (current status: %)',
      v_booking_status using errcode = 'P0001';
  end if;

  select * into v_payment
  from payment
  where company_id = p_company_id
    and payment_id = (v_booking->>'payment_id')::int
  for update;

  if found then
    if lower(v_payment.status) = 'paid' then
      perform public.refund_payment(
        p_company_id,
        v_payment.payment_id,
        null,
        'Booking cancelled'
      );
    elsif lower(v_payment.status) = 'refunded' then
      null; -- Already safely reversed; retain the audit link.
    elsif lower(v_payment.status) in ('pending', 'unpaid', 'cancelled') then
      if v_payment.redemption_id is not null then
        select * into v_redemption
        from redemption
        where company_id = p_company_id
          and redemption_id = v_payment.redemption_id
        for update;

        if found and v_redemption.status = 'Approved' then
          perform public.cancel_approved_redemption(
            p_company_id,
            v_redemption.redemption_id,
            'Booking cancelled'
          );
        elsif found and v_redemption.status = 'Pending' then
          update redemption
          set status = 'Cancelled',
              cancelled_date = current_date,
              cancelled_time = localtime,
              cancellation_reason = 'Booking cancelled'
          where company_id = p_company_id
            and redemption_id = v_redemption.redemption_id;
          update payment
          set redemption_id = null
          where company_id = p_company_id
            and payment_id = v_payment.payment_id;
        elsif found and v_redemption.status in ('Rejected', 'Cancelled', 'Refunded') then
          update payment
          set redemption_id = null
          where company_id = p_company_id
            and payment_id = v_payment.payment_id;
        elsif found then
          raise exception 'Linked redemption has an unsupported status: %',
            coalesce(v_redemption.status, '<null>') using errcode = 'P0001';
        end if;
      end if;

      update payment
      set status = 'Cancelled'
      where company_id = p_company_id
        and payment_id = v_payment.payment_id;
    else
      raise exception 'Linked payment has an unsupported status: %',
        coalesce(v_payment.status, '<null>') using errcode = 'P0001';
    end if;
  end if;

  if v_type = 'grooming' then
    update grooming_booking
    set booking_status = 'Cancelled'
    where company_id = p_company_id
      and grooming_booking_id = p_booking_id
    returning to_jsonb(grooming_booking) into v_booking;
  elsif v_type = 'daycare' then
    update daycare_booking
    set booking_status = 'Cancelled'
    where company_id = p_company_id
      and daycare_booking_id = p_booking_id
    returning to_jsonb(daycare_booking) into v_booking;
  else
    update boarding_booking
    set booking_status = 'Cancelled'
    where company_id = p_company_id
      and boarding_booking_id = p_booking_id
    returning to_jsonb(boarding_booking) into v_booking;
  end if;

  return v_booking;
end;
$$;

revoke all on function public.cancel_booking_atomic(int, text, int)
  from public, anon, authenticated;
grant execute on function public.cancel_booking_atomic(int, text, int)
  to service_role;

notify pgrst, 'reload schema';
