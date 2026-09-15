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

# GA4 gtag.js, only when a Measurement ID is set. This has to land in the
# server-rendered index.html (not via st.markdown/unsafe_allow_html — browsers
# never execute <script> tags inserted through innerHTML, which is how
# Streamlit renders unsafe markdown, so a client-side-only tag is invisible to
# Google's tag verification even though the text is technically in the DOM).
GA_SNIPPET=""
if [ -n "$GA_ID" ]; then
  GA_SNIPPET="    <script async src=\"https://www.googletagmanager.com/gtag/js?id=${GA_ID}\"></script>
    <script>
      window.dataLayer = window.dataLayer \|\| [];
      function gtag(){dataLayer.push(arguments);}
      gtag('js', new Date());
      gtag('config', '${GA_ID}');
    </script>"
fi

# Inject GA + canonical + meta tags right after <head> (before any other tags)
# Use perl for multi-line replacement (sed has portability issues with -i)
perl -i -pe 'BEGIN{undef $/;} s|(<head>\s*)|$1
'"$GA_SNIPPET"'
    <meta http-equiv="content-language" content="es-DO">
    <meta name="description" content="Guía de 56 playas de República Dominicana con alertas de sargazo en tiempo real, riesgo por playa, pronóstico de llegada, actividades y acceso.">
    <meta name="keywords" content="playas República Dominicana, sargazo RD, alerta sargazo, Dominican Republic beaches, sargassum alert, Punta Cana, Samaná, Puerto Plata, Barahona">
    <meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large">
    <meta name="author" content="Ayesha Yege">
    <meta name="geo.region" content="DO">
    <meta name="geo.placename" content="República Dominicana">
    <base href="/">
    <script>
      // Insert per-request canonical based on the current URL so
      // parameterized beach pages (e.g. ?beach=Playa+Rincon) can be
      // indexed as distinct URLs. This runs early in <head>.
      (function(){
        try {
          var c = document.createElement('link');
          c.setAttribute('rel','canonical');
          var u = location.origin + location.pathname + location.search;
          c.setAttribute('href', u);
          document.head.appendChild(c);
          // Also update any existing og:url meta to reflect the pretty path
          try {
            var og = document.querySelector('meta[property="og:url"]');
            if (og) og.setAttribute('content', u);
          } catch(e){}
        } catch(e) { /* noop */ }
      })();
    </script>
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

# Add viewport-fit=cover so CSS env(safe-area-inset-*) resolves to the real
# notch/home-indicator insets instead of always being 0. Without this, fixed
# full-bleed elements (the mobile bottom sheet, the filter drawer) render
# flush against the physical screen edge on notched phones. Streamlit ships
# this tag across multiple lines, so the match has to slurp the whole file
# (BEGIN{undef $/;}) rather than process line-by-line like the -pe default.
perl -0777 -i -pe 's|<meta\s+name="viewport"\s+content="[^"]*"\s*/?>|<meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no, viewport-fit=cover" />|s' "$INDEX_HTML"

echo "✓ SEO tags injected into $INDEX_HTML"
echo "  Canonical: $CANONICAL_URL"
echo "  GA: ${GA_ID:-<none>}"

# Start Streamlit
exec streamlit run dashboard/beaches.py \
  --server.port="${PORT:-8501}" \
  --server.address=0.0.0.0 \
  --server.headless=true \
  --browser.gatherUsageStats=false
