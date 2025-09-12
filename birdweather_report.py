import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
import requests
from jinja2 import Template
import argparse
import logging
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)

def fetch_bird_detections(hours=24):
    detections = []
    now = datetime.now(timezone.utc)
    from_time = (now - timedelta(hours=hours)).isoformat()
    logging.info(f"Fetching detections from {from_time}")
    
    url = f"https://app.birdweather.com/api/v1/stations/{config.STATION_TOKEN}/detections"
    params = {
        "from": from_time,
        "limit": 100,
        "order": "desc"
    }
    
    while True:
        response = requests.get(url, params=params)
        data = response.json()
        
        if not data['success'] or not data['detections']:
            break
            
        logging.info(f"Fetched {len(data['detections'])} detections")
            
        detections.extend(data['detections'])
        last_id = data['detections'][-1]['id']
        
        if len(data['detections']) < 100:
            break
            
        params['cursor'] = last_id
    
    return detections

def generate_report(hours=24):
    detections = fetch_bird_detections(hours)
    species_stats = defaultdict(lambda: {
        'count': 0,
        'max_confidence': 0,
        'hour_counts': defaultdict(int),
        'scientific_name': None,
        'image_url': None,
        'best_soundscape': None
    })
    
    for detection in detections:
        common_name = detection['species']['commonName']
        hour = datetime.fromisoformat(detection['timestamp']).replace(tzinfo=None).hour
        stats = species_stats[common_name]
        stats['count'] += 1
        stats['scientific_name'] = detection['species']['scientificName']
        stats['image_url'] = detection['species']['imageUrl']
        
        confidence = detection['confidence']
        if confidence > stats['max_confidence']:
            stats['max_confidence'] = confidence
            # Only store soundscape if it has a valid URL
            soundscape = detection.get('soundscape')
            if soundscape and soundscape.get('url'):
                stats['best_soundscape'] = soundscape
            
        stats['hour_counts'][hour] += 1
    
    sorted_species = sorted(
        [{'name': k, **v} for k, v in species_stats.items()],
        key=lambda x: x['count'],
        reverse=True
    )
    
    for species in sorted_species:
        species['hours'] = [
            {
                'count': species['hour_counts'].get(hour, 0),
                'intensity': min(species['hour_counts'].get(hour, 0) * 20, 100)
            }
            for hour in range(24)
        ]
    
    template = Template("""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body { 
                font-family: Arial, sans-serif; 
                max-width: 1000px; 
                margin: 0 auto; 
                padding: 20px; 
            }
            .species-card { 
                border: 1px solid #ddd; 
                border-radius: 8px; 
                padding: 20px; 
                margin-bottom: 20px; 
            }
            .species-header { 
                display: flex; 
                gap: 20px; 
                margin-bottom: 15px; 
            }
            .species-image { 
                width: 150px; 
                height: 150px; 
                object-fit: cover; 
                border-radius: 8px; 
            }
            .species-info { 
                flex: 1; 
            }
            .species-name { 
                font-size: 20px; 
                font-weight: bold; 
                margin-bottom: 5px; 
            }
            .scientific-name { 
                font-style: italic; 
                color: #666; 
            }
            .hour-grid { 
                display: flex; 
                gap: 2px; 
                margin-top: 10px; 
            }
            .hour-cell { 
                width: 30px; 
                height: 30px; 
                border: 1px solid #eee;
                display: flex;
                align-items: center;
                justify-content: center;
                position: relative;
            }
            .hour-number {
                font-size: 11px;
                font-weight: 500;
            }
            .sound-link { 
                display: inline-block; 
                margin-top: 10px; 
                color: #0066cc; 
                text-decoration: none; 
            }
        </style>
    </head>
    <body>
        <h1>Bird Detection Report - Past {{ hours }} Hours</h1>
        <h2>{{ species_list|length }} Species, {{ species_list|sum(attribute='count') }} Total Detections</h2>
        <p>Generated on {{ generated_on }} from <a href="{{ friendly_url }}">BirdWeather station {{config.STATION_TOKEN}}</a></p>
        {% for species in species_list %}
        <div class="species-card">
            <div class="species-header">
                {% if species.image_url %}
                <img src="{{ species.image_url }}" alt="{{ species.name }}" class="species-image">
                {% endif %}
                <div class="species-info">
                    <div class="species-name">{{ species.name }}</div>
                    <div class="scientific-name">{{ species.scientific_name }}</div>
                    <div>Detections: {{ species.count }}</div>
                    <div>Highest confidence: {{ "%.1f"|format(species.max_confidence * 100) }}%</div>
                    {% if species.best_soundscape and species.best_soundscape.url %}
                    <a href="{{ species.best_soundscape.url }}" class="sound-link">
                        Listen to best detection ({{ "%.1f"|format(species.best_soundscape.startTime) }}s - {{ "%.1f"|format(species.best_soundscape.endTime) }}s)
                    </a>
                    {% endif %}
                </div>
            </div>
            
            <div class="hour-grid">
                {% for hour in species.hours %}
                <div class="hour-cell" 
                     style="background-color: rgba(0, 100, 0, {{ hour.intensity / 100 }});" 
                     title="{{ hour.count }} detections">
                    <span class="hour-number" style="color: {{ 'white' if hour.intensity > 50 else 'black' }}">
                        {{loop.index0}}
                    </span>
                </div>
                {% endfor %}
            </div>
        </div>
        {% endfor %}
    </body>
    </html>
    """)
    
    return template.render(
        species_list=sorted_species,
        hours=hours,
        generated_on=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        friendly_url= f"https://app.birdweather.com/stations/{config.STATION_TOKEN}",
        config=config
    )

def send_email(html_content):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = "BirdWeather Report 🦉"
    msg['From'] = config.EMAIL_FROM
    
    # to and cc can be either simple emails or lists of emails ['email1','email2']
    # also any of these emails can be parsed from 'Your Name <youremail@domain>'
    to_recipients = config.EMAIL_TO if isinstance(config.EMAIL_TO, list) else [config.EMAIL_TO]
    cc_recipients = config.EMAIL_CC if isinstance(config.EMAIL_CC, list) else [config.EMAIL_CC]

    msg['To'] = ", ".join(to_recipients)
    msg['Cc'] = ", ".join(cc_recipients)

    # Extract just the email addresses
    recipient_addresses = [parseaddr(addr)[1] for addr in to_recipients + cc_recipients]

    msg.attach(MIMEText(html_content, 'html'))

    with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as server:
        server.starttls()
        server.login(config.EMAIL_FROM, config.EMAIL_PASSWORD)
        server.sendmail(config.EMAIL_FROM, recipient_addresses, msg.as_string())  # Send to extracted addresses


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate bird detection report')
    parser.add_argument('--hours', type=int, default=24,
                      help='Number of hours to include in report (default: 24)')
    args = parser.parse_args()
    
    logging.info(f"Starting report generation for past {args.hours} hours")
    html_report = generate_report(args.hours)
    logging.info("Generated HTML report, sending email")
    send_email(html_report)
    logging.info("Email sent successfully")
