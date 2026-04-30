-- Users table
create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  phone_number text not null unique,
  name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Appointments table
create table if not exists public.appointments (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references public.users(id) on delete cascade,
  phone_number text not null,
  user_name text,
  appointment_time timestamptz not null,
  intent text,
  status text not null default 'confirmed' check (status in ('confirmed','cancelled','completed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Prevent double booking for active appointments
create unique index if not exists appointments_unique_active_slot
on public.appointments (appointment_time)
where status = 'confirmed';

create index if not exists appointments_phone_idx on public.appointments (phone_number);
create index if not exists appointments_time_idx on public.appointments (appointment_time);

-- Call summaries table
create table if not exists public.call_summaries (
  id uuid primary key default gen_random_uuid(),
  user_phone text,
  user_name text,
  duration_seconds int,
  transcript jsonb,
  appointments_booked jsonb,
  preferences jsonb,
  cost_breakdown jsonb,
  summary_text text,
  ended_at timestamptz,
  created_at timestamptz not null default now()
);

-- Safe migrations for existing Supabase projects where the table already exists.
alter table public.call_summaries add column if not exists user_name text;
alter table public.call_summaries add column if not exists appointments_booked jsonb;
alter table public.call_summaries add column if not exists preferences jsonb;
alter table public.call_summaries add column if not exists cost_breakdown jsonb;
alter table public.call_summaries add column if not exists ended_at timestamptz;
