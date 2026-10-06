-- Tabelle fuer die Warteliste der Landingpage (landing/lib/waitlist-store.ts).
-- Einmal im SQL-Editor des Supabase-Projekts ausfuehren.

create table if not exists public.waitlist (
  id bigint generated always as identity primary key,
  email text not null unique check (char_length(email) <= 254),
  name text check (char_length(name) <= 80),
  role text check (role in ('schueler', 'eltern', 'lehrkraft', 'andere')),
  platform text check (platform in ('windows', 'macos', 'server')),
  source text check (char_length(source) <= 64),
  created_at timestamptz not null default now()
);

-- Nur der Server schreibt, mit dem Service-Role-Schluessel. RLS ohne Policy
-- sperrt den oeffentlichen anon-Schluessel vollstaendig aus.
alter table public.waitlist enable row level security;
