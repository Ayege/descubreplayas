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
    eta_timestamp   timestamptz
);

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

-- -------------------------------------------------------------------------
-- Indexes
-- -------------------------------------------------------------------------
create index if not exists detections_run_at_idx  on detections (run_at desc);
create index if not exists forecasts_run_at_idx   on forecasts  (run_at desc);
create index if not exists forecasts_zone_id_idx  on forecasts  (zone_id);
create index if not exists detections_geom_idx    on detections using gist (geom);
create index if not exists zones_geom_idx         on zones      using gist (geom);
create index if not exists beaches_geom_idx        on beaches    using gist (geom);
create index if not exists beaches_province_idx    on beaches    (province);
create index if not exists beaches_region_idx      on beaches    (region);

-- -------------------------------------------------------------------------
-- Seed zones (5 coastal zones as small boxes ± 0.1° around each centre)
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
  )
on conflict (name) do nothing;
