-- Database schema: PostGIS extension, tables, and seed zones.
-- Run this in the Supabase SQL editor (Database > SQL Editor > New query).
-- Safe to re-run: all statements use IF NOT EXISTS / ON CONFLICT DO NOTHING.

-- -------------------------------------------------------------------------
-- Extensions
-- -------------------------------------------------------------------------
create extension if not exists postgis;

-- -------------------------------------------------------------------------
-- Tables
-- -------------------------------------------------------------------------

create table if not exists zones (
    id          serial primary key,
    name        text not null unique,
    geom        geometry(polygon, 4326) not null,
    center_lat  double precision not null,
    center_lon  double precision not null
);

create table if not exists detections (
    id          bigserial primary key,
    run_at      timestamptz not null default now(),
    geom        geometry(polygon, 4326) not null,
    centroid    geometry(point, 4326) not null,
    area_km2    double precision not null,
    source      text not null default 'sentinel-2'
);

create table if not exists forecasts (
    id              bigserial primary key,
    run_at          timestamptz not null default now(),
    zone_id         int not null references zones(id) on delete cascade,
    risk_level      text not null check (risk_level in ('none', 'low', 'medium', 'high')),
    eta_hours       int,
    eta_timestamp   timestamptz,
    horizons        jsonb
);

-- Forecast horizon breakdown (added after initial release; safe to re-run).
alter table forecasts add column if not exists horizons jsonb;

-- Particle-ensemble drift (safe to re-run): share of ensemble members in
-- which sargassum reaches the zone's coast within 72 h, and the share at
-- each risk level ({"none": .., "low": .., "medium": .., "high": ..}).
alter table forecasts add column if not exists arrival_probability double precision;
alter table forecasts add column if not exists risk_probs jsonb;

-- When each patch was actually seen (detections can be days old), and the
-- estimated sargassum-covered area inside its outline.
alter table detections add column if not exists observed_at timestamptz;
alter table detections add column if not exists covered_km2 double precision;

create table if not exists subscribers (
    id              bigserial primary key,
    channel         text not null default 'telegram',
    chat_id         text not null,
    zone_id         int not null references zones(id) on delete cascade,
    role            text not null default 'subscriber',
    last_alerted    timestamptz,
    created_at      timestamptz not null default now(),
    unique (channel, chat_id, zone_id)
);

create table if not exists beaches (
    id                  bigserial primary key,
    name                text not null unique,
    province            text not null,
    region              text not null,
    latitude            double precision not null,
    longitude           double precision not null,
    geom                geometry(point, 4326),
    access_type         text,
    access_description   text,
    entrance_fee        text,
    parking             boolean default true,
    beach_type          text[] default '{}',
    activities          text[] default '{}',
    wildlife            text[] default '{}',
    ecosystem           text,
    protected_area      boolean default false,
    facilities          text[] default '{}',
    water_conditions    text,
    best_time_to_visit  text,
    description         text,
    google_maps_url     text
);

-- Extended ML forecasts (7 / 14 / 21 day horizons)
create table if not exists ml_forecasts (
    id          bigserial primary key,
    run_at      timestamptz not null default now(),
    zone_id     int not null references zones(id) on delete cascade,
    lead_days   int not null check (lead_days in (7, 14, 21)),
    risk_level  text not null check (risk_level in ('none', 'low', 'medium', 'high')),
    confidence  double precision not null default 0.0,
    method      text not null default 'seasonal',
    valid_at    date not null,
    unique (run_at, zone_id, lead_days)
);

-- Calibrated class probabilities of the extended forecast (safe to re-run).
alter table ml_forecasts add column if not exists probs jsonb;

alter table ml_forecasts enable row level security;

do $$ begin
  if not exists (
    select 1 from pg_policies
    where tablename = 'ml_forecasts' and policyname = 'ml_forecasts_public_read'
  ) then
    create policy ml_forecasts_public_read on ml_forecasts for select using (true);
  end if;
end $$;

-- -------------------------------------------------------------------------
-- Ground truth and evaluation (added for forecast scoring; safe to re-run)
-- -------------------------------------------------------------------------

-- What visitors actually saw on the beach, sent from the beach explorer via
-- POST /reports. This is the only real observation of arrivals the system
-- has; everything else is a model output. No personal data is stored: no IP,
-- no account, no free text.
create table if not exists beach_reports (
    id           bigserial primary key,
    created_at   timestamptz not null default now(),
    observed_on  date not null,
    beach_name   text not null,
    latitude     double precision not null,
    longitude    double precision not null,
    zone_id      int references zones(id) on delete set null,
    amount       text not null check (amount in ('none', 'low', 'medium', 'high')),
    source       text not null default 'web'
);

-- Per-run, per-zone detection aggregates: exactly the inputs the 7/14/21-day
-- model uses, written by the pipeline with the same code at training and
-- prediction time. Training reads this small table instead of re-deriving
-- features from the (very large) detections table.
create table if not exists zone_features (
    run_at          timestamptz not null,
    zone_id         int not null references zones(id) on delete cascade,
    eez_area_km2    double precision not null,
    zone_area_km2   double precision not null,
    nearest_km      double precision not null,
    patch_count     int not null,
    physics_risk    int not null check (physics_risk between 0 and 3),
    primary key (run_at, zone_id)
);

-- One row per pipeline run: how far the Sentinel-2 detections agree with the
-- NOAA/USF daily AFAI product, and how much AFAI filled in.
create table if not exists detection_checks (
    id                   bigserial primary key,
    run_at               timestamptz not null default now(),
    afai_dates           text[] not null default '{}',
    afai_clear_frac      double precision,
    s2_patches           int not null default 0,
    s2_checkable         int not null default 0,
    s2_confirmed         int not null default 0,
    afai_clusters        int not null default 0,
    afai_matched         int not null default 0,
    gap_filled           int not null default 0,
    gap_filled_area_km2  double precision not null default 0
);

-- One row per model training: walk-forward test scores for the model and its
-- baselines, and whether the model was good enough to be used.
create table if not exists model_evaluations (
    id                bigserial primary key,
    run_at            timestamptz not null default now(),
    n_rows            int not null,
    n_folds           int not null,
    metrics           jsonb not null,
    beats_seasonal    boolean not null,
    used_for_predict  boolean not null
);

alter table beach_reports     enable row level security;
alter table zone_features     enable row level security;
alter table detection_checks  enable row level security;
alter table model_evaluations enable row level security;

do $$ begin
  -- beach_reports: no public read policy. Only the service role (API,
  -- pipeline, evaluation) can read or write reports.
  if not exists (
    select 1 from pg_policies where tablename = 'zone_features' and policyname = 'zone_features_public_read'
  ) then
    create policy zone_features_public_read on zone_features for select using (true);
  end if;
  if not exists (
    select 1 from pg_policies where tablename = 'detection_checks' and policyname = 'detection_checks_public_read'
  ) then
    create policy detection_checks_public_read on detection_checks for select using (true);
  end if;
  if not exists (
    select 1 from pg_policies where tablename = 'model_evaluations' and policyname = 'model_evaluations_public_read'
  ) then
    create policy model_evaluations_public_read on model_evaluations for select using (true);
  end if;
end $$;

-- -------------------------------------------------------------------------
-- Model versions, beach-level forecasts, zone coastlines (safe to re-run)
-- -------------------------------------------------------------------------

-- Which model produced each row, so scores never mix models. Rows from
-- before versioning are marked 'legacy' (centroid-in-box drift, wind read in
-- km/h as m/s).
alter table forecasts     add column if not exists model_version text;
alter table ml_forecasts  add column if not exists model_version text;
alter table zone_features add column if not exists model_version text;
update forecasts     set model_version = 'legacy' where model_version is null;
update ml_forecasts  set model_version = 'legacy' where model_version is null;
update zone_features set model_version = 'legacy' where model_version is null;
alter table zone_features alter column model_version set not null;

-- zone_features can now hold the same run twice: the legacy row and one
-- regenerated with the current drift model (pipeline/replay.py).
do $$ begin
  if exists (
    select 1 from pg_constraint
    where conname = 'zone_features_pkey'
      and pg_get_constraintdef(oid) not like '%model_version%'
  ) then
    alter table zone_features drop constraint zone_features_pkey;
    alter table zone_features add primary key (run_at, zone_id, model_version);
  end if;
end $$;

-- When a report was sent (time of day matters: arrivals change within a day).
-- Null for reports about yesterday.
alter table beach_reports add column if not exists observed_at timestamptz;

-- The pipeline's own forecast for each catalogue beach: particles stopping
-- within BEACH_RADIUS_KM of the beach. This is what the beach card shows when
-- available, and what visitor reports (which are per beach) are scored on.
create table if not exists beach_forecasts (
    id                   bigserial primary key,
    run_at               timestamptz not null default now(),
    beach_name           text not null,
    zone_id              int references zones(id) on delete set null,
    risk_level           text not null check (risk_level in ('none', 'low', 'medium', 'high')),
    eta_hours            int,
    arrival_probability  double precision,
    risk_probs           jsonb,
    horizons             jsonb,
    model_version        text not null
);
alter table beach_forecasts enable row level security;
do $$ begin
  if not exists (
    select 1 from pg_policies where tablename = 'beach_forecasts' and policyname = 'beach_forecasts_public_read'
  ) then
    create policy beach_forecasts_public_read on beach_forecasts for select using (true);
  end if;
end $$;

-- The stretch of coast each zone owns (arrivals are counted there). `geom`
-- keeps the original display boxes, which no longer decide anything.
alter table zones add column if not exists coast geometry(multilinestring, 4326);

-- -------------------------------------------------------------------------
-- Indexes
-- -------------------------------------------------------------------------
create index if not exists beach_forecasts_run_idx    on beach_forecasts (run_at desc);
create index if not exists beach_forecasts_beach_idx  on beach_forecasts (beach_name, run_at desc);
create index if not exists beach_reports_observed_idx on beach_reports (observed_on, zone_id);
create index if not exists zone_features_run_at_idx   on zone_features (run_at);
create index if not exists detections_run_at_idx    on detections   (run_at desc);
create index if not exists forecasts_run_at_idx     on forecasts    (run_at desc);
create index if not exists forecasts_zone_id_idx    on forecasts    (zone_id);
create index if not exists ml_forecasts_run_at_idx  on ml_forecasts (run_at desc);
create index if not exists ml_forecasts_zone_idx    on ml_forecasts (zone_id, lead_days);
create index if not exists detections_geom_idx    on detections using gist (geom);
create index if not exists zones_geom_idx         on zones      using gist (geom);
create index if not exists beaches_geom_idx        on beaches    using gist (geom);
create index if not exists beaches_province_idx    on beaches    (province);
create index if not exists beaches_region_idx      on beaches    (region);

-- -------------------------------------------------------------------------
-- Row Level Security
-- All tables are readable by the public anon role.
-- Write operations (INSERT/UPDATE/DELETE) require the service_role key,
-- which bypasses RLS entirely — no extra policy needed for writes.
-- -------------------------------------------------------------------------
alter table zones        enable row level security;
alter table detections   enable row level security;
alter table forecasts    enable row level security;
alter table subscribers  enable row level security;
alter table beaches      enable row level security;

-- Public read policies (anon key can read everything → dashboard & API work)
do $$ begin
  if not exists (
    select 1 from pg_policies where tablename = 'zones' and policyname = 'zones_public_read'
  ) then
    create policy zones_public_read        on zones       for select using (true);
  end if;
  if not exists (
    select 1 from pg_policies where tablename = 'detections' and policyname = 'detections_public_read'
  ) then
    create policy detections_public_read   on detections  for select using (true);
  end if;
  if not exists (
    select 1 from pg_policies where tablename = 'forecasts' and policyname = 'forecasts_public_read'
  ) then
    create policy forecasts_public_read    on forecasts   for select using (true);
  end if;
  if not exists (
    select 1 from pg_policies where tablename = 'beaches' and policyname = 'beaches_public_read'
  ) then
    create policy beaches_public_read      on beaches     for select using (true);
  end if;
  -- subscribers: only readable by service_role (no public read policy)
end $$;

-- -------------------------------------------------------------------------
-- Seed zones (14 coastal zones; the pipeline also inserts any zone in
-- pipeline/config.py that is missing here)
-- -------------------------------------------------------------------------
insert into zones (name, center_lat, center_lon, geom) values
  (
    'Punta Cana', 18.58, -68.37,
    st_geomfromtext(
      'POLYGON((-68.47 18.48, -68.27 18.48, -68.27 18.68, -68.47 18.68, -68.47 18.48))',
      4326
    )
  ),
  (
    'Bavaro', 18.68, -68.43,
    st_geomfromtext(
      'POLYGON((-68.53 18.58, -68.33 18.58, -68.33 18.78, -68.53 18.78, -68.53 18.58))',
      4326
    )
  ),
  (
    'Samana', 19.20, -69.33,
    st_geomfromtext(
      'POLYGON((-69.43 19.10, -69.23 19.10, -69.23 19.30, -69.43 19.30, -69.43 19.10))',
      4326
    )
  ),
  (
    'Puerto Plata', 19.80, -70.69,
    st_geomfromtext(
      'POLYGON((-70.79 19.70, -70.59 19.70, -70.59 19.90, -70.79 19.90, -70.79 19.70))',
      4326
    )
  ),
  (
    'Juan Dolio', 18.43, -69.42,
    st_geomfromtext(
      'POLYGON((-69.52 18.33, -69.32 18.33, -69.32 18.53, -69.52 18.53, -69.52 18.33))',
      4326
    )
  ),
  (
    'Barahona', 18.10, -71.10,
    st_geomfromtext(
      'POLYGON((-71.20 18.00, -71.00 18.00, -71.00 18.20, -71.20 18.20, -71.20 18.00))',
      4326
    )
  ),
  (
    'Pedernales', 17.90, -71.65,
    st_geomfromtext(
      'POLYGON((-71.75 17.80, -71.55 17.80, -71.55 18.00, -71.75 18.00, -71.75 17.80))',
      4326
    )
  ),
  (
    'Monte Cristi', 19.86, -71.65,
    st_geomfromtext(
      'POLYGON((-71.75 19.76, -71.55 19.76, -71.55 19.96, -71.75 19.96, -71.75 19.76))',
      4326
    )
  ),
  (
    'Rio San Juan', 19.62, -70.08,
    st_geomfromtext(
      'POLYGON((-70.18 19.52, -69.98 19.52, -69.98 19.72, -70.18 19.72, -70.18 19.52))',
      4326
    )
  ),
  (
    'Azua', 18.22, -70.45,
    st_geomfromtext(
      'POLYGON((-70.55 18.12, -70.35 18.12, -70.35 18.32, -70.55 18.32, -70.55 18.12))',
      4326
    )
  ),
  (
    'Punta Rucia', 19.87, -71.21,
    st_geomfromtext(
      'POLYGON((-71.56 19.52, -70.86 19.52, -70.86 20.22, -71.56 20.22, -71.56 19.52))',
      4326
    )
  ),
  (
    'Santo Domingo', 18.45, -69.95,
    st_geomfromtext(
      'POLYGON((-70.30 18.10, -69.60 18.10, -69.60 18.80, -70.30 18.80, -70.30 18.10))',
      4326
    )
  ),
  (
    'Miches', 18.99, -68.85,
    st_geomfromtext(
      'POLYGON((-69.20 18.64, -68.50 18.64, -68.50 19.34, -69.20 19.34, -69.20 18.64))',
      4326
    )
  ),
  (
    'La Romana', 18.30, -68.76,
    st_geomfromtext(
      'POLYGON((-69.11 17.95, -68.41 17.95, -68.41 18.65, -69.11 18.65, -69.11 17.95))',
      4326
    )
  )
on conflict (name) do nothing;

-- -------------------------------------------------------------------------
-- View: detections_latest
-- Exposes the most-recent pipeline run's sargassum patches as plain
-- lat/lon + area so the dashboard can plot the detected masses without
-- needing PostGIS geometry parsing on the client.
-- -------------------------------------------------------------------------
create or replace view detections_latest as
select
    id,
    run_at,
    st_y(centroid) as lat,
    st_x(centroid) as lon,
    area_km2,
    source,
    observed_at,
    covered_km2
from detections
where run_at = (select max(run_at) from detections);

-- -------------------------------------------------------------------------
-- View: forecasts_latest
-- One row per zone: its most recent forecast, with the zone name joined in
-- directly (plain column, not a PostgREST embed — views don't carry the
-- foreign-key metadata PostgREST needs to embed related tables). Moves the
-- "latest per zone" dedup into SQL instead of over-fetching N rows and
-- deduplicating in Python against a hardcoded limit that assumed a fixed
-- zone count and run cadence (see api/db.py history).
-- -------------------------------------------------------------------------
create or replace view forecasts_latest as
select
    f.id,
    f.run_at,
    f.zone_id,
    z.name as zone_name,
    f.risk_level,
    f.eta_hours,
    f.eta_timestamp,
    f.horizons,
    f.arrival_probability,
    f.risk_probs,
    f.model_version
from forecasts f
join zones z on z.id = f.zone_id
where f.run_at = (
    select max(f2.run_at) from forecasts f2 where f2.zone_id = f.zone_id
);

-- -------------------------------------------------------------------------
-- View: ml_forecasts_latest
-- One row per (zone, lead_days): its most recent ML forecast. Same rationale
-- as forecasts_latest above.
-- -------------------------------------------------------------------------
create or replace view ml_forecasts_latest as
select
    m.id,
    m.run_at,
    m.zone_id,
    z.name as zone_name,
    m.lead_days,
    m.risk_level,
    m.confidence,
    m.method,
    m.valid_at,
    m.probs,
    m.model_version
from ml_forecasts m
join zones z on z.id = m.zone_id
where m.run_at = (
    select max(m2.run_at) from ml_forecasts m2
    where m2.zone_id = m.zone_id and m2.lead_days = m.lead_days
);


-- -------------------------------------------------------------------------
-- View: beach_forecasts_latest
-- One row per beach: its most recent pipeline forecast.
-- -------------------------------------------------------------------------
create or replace view beach_forecasts_latest as
select b.*
from beach_forecasts b
where b.run_at = (
    select max(b2.run_at) from beach_forecasts b2 where b2.beach_name = b.beach_name
);
