---
sitemap: false
---
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Daily Press</title>
  <link rel="icon" type="image/png" href="{{ '/assets/prc-favicon.png' | relative_url }}">
  {% assign latest = site.posts | where: "lang", "en" | first %}
  {% if latest %}
  <meta http-equiv="refresh" content="0; url={{ latest.url | relative_url }}" />
  <link rel="canonical" href="{{ latest.url | absolute_url }}" />
  <script>window.location.replace({{ latest.url | relative_url | jsonify }});</script>
  {% else %}
  <meta http-equiv="refresh" content="0; url={{ '/archive/' | relative_url }}" />
  {% endif %}
</head>
<body style="font-family:system-ui,sans-serif;background:#ebeadf;color:#1c1a17;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;">
  <p>Opening the latest digest… {% if latest %}<a href="{{ latest.url | relative_url }}" style="color:#3c0114;">continue &rarr;</a>{% endif %}</p>
</body>
</html>
