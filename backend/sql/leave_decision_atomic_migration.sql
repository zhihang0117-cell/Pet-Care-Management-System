-- Prevent approving leave that would silently invalidate assigned bookings.

create or replace function public.decide_leave_request(
  p_company_id int,
  p_leave_id int,
  p_status text,
  p_reviewed_by int default null,
  p_reviewed_date date default current_date,
  p_reviewed_time time default localtime
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_leave leave%rowtype;
  v_result jsonb;
begin
  if p_status not in ('Approved', 'Rejected') then
    raise exception 'status must be Approved or Rejected' using errcode = 'P0001';
  end if;

  select * into v_leave
  from leave
  where company_id = p_company_id and leave_id = p_leave_id
  for update;
  if not found then
    raise exception 'Leave request not found' using errcode = 'P0002';
  end if;
  if v_leave.status <> 'Pending' then
    raise exception 'Only a pending leave request can be reviewed' using errcode = 'P0001';
  end if;

  if p_status = 'Approved' then
    perform pg_advisory_xact_lock(
      hashtextextended(p_company_id::text || ':staff:' || v_leave.staff_id::text, 0)
    );
    if exists (
      select 1 from grooming_booking b
      where b.company_id = p_company_id and b.staff_id = v_leave.staff_id
        and b.booking_status in ('Pending', 'Scheduled')
        and b.booking_date between v_leave.start_date and v_leave.end_date
      union all
      select 1 from daycare_booking b
      where b.company_id = p_company_id and b.staff_id = v_leave.staff_id
        and b.booking_status in ('Pending', 'Scheduled')
        and b.booking_date between v_leave.start_date and v_leave.end_date
      union all
      select 1 from boarding_booking b
      where b.company_id = p_company_id and b.staff_id = v_leave.staff_id
        and b.booking_status in ('Pending', 'Scheduled')
        and (
          b.check_in_date between v_leave.start_date and v_leave.end_date
          or b.check_out_date between v_leave.start_date and v_leave.end_date
        )
    ) then
      raise exception 'Reassign or reschedule this staff member''s active bookings before approving leave'
        using errcode = 'P0001';
    end if;
  end if;

  update leave
  set status = p_status,
      reviewed_by_staff_id = p_reviewed_by,
      reviewed_date = p_reviewed_date,
      reviewed_time = p_reviewed_time
  where company_id = p_company_id and leave_id = p_leave_id
  returning to_jsonb(leave) into v_result;
  return v_result;
end;
$$;

revoke all on function public.decide_leave_request(int, int, text, int, date, time)
  from public, anon, authenticated;
grant execute on function public.decide_leave_request(int, int, text, int, date, time)
  to service_role;

notify pgrst, 'reload schema';
