import requests
import datetime

# URL of the RSS feed
rss_url = "https://www.tagesschau.de/index~rss2.xml"

# Fetch the RSS feed
response = requests.get(rss_url)

# Get the current date and time
current_time = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

# Filename with the current date and time
filename = f"rss_feed_{current_time}.xml"

# Save the RSS feed to a file
with open(filename, "w", encoding="utf-8") as file:
    file.write(response.text)

print(f"RSS feed saved as {filename}")
