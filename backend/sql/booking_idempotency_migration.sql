-- Database-level idempotency for AI booking creation.
--
-- The existing create_booking_atomic() function remains the single source of
-- truth for validation and writes. This wrapper remembers its committed JSON
-- result under a server-generated key, so retrying after an HTTP/tool timeout
-- returns the original booking instead of inserting a second one.

create table if not exists public.ai_mutation_idempotency (
  company_id int not null references public.companies(company_id) on delete cascade,
  operation text not null,
  idempotency_key text not null,
  request_fingerprint text not null,
  result jsonb not null,
  created_at timestamptz not null default now(),
  primary key (company_id, operation, idempotency_key),
  constraint ai_mutation_idempotency_key_length
    check (char_length(idempotency_key) between 16 and 128)
);

alter table public.ai_mutation_idempotency enable row level security;
revoke all on table public.ai_mutation_idempotency from public, anon, authenticated;
revoke all on table public.ai_mutation_idempotency from service_role;
grant select on table public.ai_mutation_idempotency to service_role;

create or replace function public.create_booking_idempotent(
  p_company_id int,
  p_booking_type text,
  p_booking jsonb,
  p_payment jsonb,
  p_idempotency_key text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_existing public.ai_mutation_idempotency%rowtype;
  v_fingerprint text;
  v_result jsonb;
begin
  if p_idempotency_key is null
    or char_length(btrim(p_idempotency_key)) < 16
    or char_length(btrim(p_idempotency_key)) > 128 then
    raise exception 'A valid booking idempotency key is required' using errcode = 'P0001';
  end if;

  v_fingerprint := md5(
    coalesce(p_booking_type, '') || ':' ||
    (
      coalesce(p_booking, '{}'::jsonb)
      - 'created_date'
      - 'created_time'
    )::text || ':' ||
    (coalesce(p_payment, '{}'::jsonb) - 'date')::text
  );

  -- Serialize retries for this exact logical request. The inner booking RPC
  -- still owns staff/pet/room locks and every business-rule check.
  perform pg_advisory_xact_lock(
    hashtextextended(
      p_company_id::text || ':create_booking:' || btrim(p_idempotency_key),
      0
    )
  );

  select * into v_existing
  from public.ai_mutation_idempotency
  where company_id = p_company_id
    and operation = 'create_booking'
    and idempotency_key = btrim(p_idempotency_key);

  if found then
    if v_existing.request_fingerprint <> v_fingerprint then
      raise exception 'Booking idempotency key was reused with different details'
        using errcode = 'P0001';
    end if;
    return v_existing.result;
  end if;

  v_result := public.create_booking_atomic(
    p_company_id,
    p_booking_type,
    p_booking,
    p_payment
  );
  if v_result is null or not (v_result ? 'booking') then
    raise exception 'create_booking_atomic returned no booking' using errcode = 'P0001';
  end if;

  insert into public.ai_mutation_idempotency (
    company_id,
    operation,
    idempotency_key,
    request_fingerprint,
    result
  ) values (
    p_company_id,
    'create_booking',
    btrim(p_idempotency_key),
    v_fingerprint,
    v_result
  );

  return v_result;
end;
$$;

revoke all on function public.create_booking_idempotent(int, text, jsonb, jsonb, text)
  from public, anon, authenticated;
grant execute on function public.create_booking_idempotent(int, text, jsonb, jsonb, text)
  to service_role;

notify pgrst, 'reload schema';
