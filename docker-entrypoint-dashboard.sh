#!/bin/bash
set -e

# Inject SEO meta tags into Streamlit's index.html template at container startup.
# This ensures Google sees the canonical link + OG tags in the INITIAL HTTP response,
# not after JavaScript hydration. The env var APP_CANONICAL_URL must be set at runtime.

CANONICAL_URL="${APP_CANONICAL_URL:-https://descubreplayas.com.do}"
GA_ID="${GA_MEASUREMENT_ID:-}"
INDEX_HTML="/usr/local/lib/python3.11/site-packages/streamlit/static/index.html"

if [ ! -f "$INDEX_HTML" ]; then
  echo "ERROR: Streamlit index.html not found at $INDEX_HTML"
  exit 1
fi

# Backup original if not already done
[ ! -f "$INDEX_HTML.orig" ] && cp "$INDEX_HTML" "$INDEX_HTML.orig"

# Inject canonical + meta tags right after <head> (before any other tags)
# Use perl for multi-line replacement (sed has portability issues with -i)
perl -i -pe 'BEGIN{undef $/;} s|(<head>\s*)|$1
    <meta http-equiv="content-language" content="es-DO">
    <meta name="description" content="Guía de 56 playas de República Dominicana con alertas de sargazo en tiempo real, riesgo por playa, pronóstico de llegada, actividades y acceso.">
    <meta name="keywords" content="playas República Dominicana, sargazo RD, alerta sargazo, Dominican Republic beaches, sargassum alert, Punta Cana, Samaná, Puerto Plata, Barahona">
    <meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large">
    <meta name="author" content="Ayesha Yege">
    <meta name="geo.region" content="DO">
    <meta name="geo.placename" content="República Dominicana">
    <link rel="canonical" href="'"$CANONICAL_URL"'">
    <meta property="og:type" content="website">
    <meta property="og:url" content="'"$CANONICAL_URL"'">
    <meta property="og:title" content="Descubre Playas RD 🌴 — 56 Playas + Alertas de Sargazo">
    <meta property="og:description" content="Guía de playas de República Dominicana con alertas de sargazo en tiempo real.">
    <meta property="og:locale" content="es_DO">
    <meta property="og:site_name" content="Descubre Playas RD">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="Descubre Playas RD">
    <meta name="twitter:description" content="56 playas RD con alertas de sargazo en tiempo real">
    |smg' "$INDEX_HTML"

# Also update the <title> tag
perl -i -pe 's|<title>.*?</title>|<title>Descubre Playas RD 🌴</title>|' "$INDEX_HTML"

# Add lang attribute to <html>
perl -i -pe 's|<html[^>]*>|<html lang="es-DO">|' "$INDEX_HTML"

echo "✓ SEO tags injected into $INDEX_HTML"
echo "  Canonical: $CANONICAL_URL"

# Start Streamlit
exec streamlit run dashboard/beaches.py \
  --server.port="${PORT:-8501}" \
  --server.address=0.0.0.0 \
  --server.headless=true \
  --browser.gatherUsageStats=false
