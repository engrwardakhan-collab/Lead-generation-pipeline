# Add City to Scraper

Guide the user through adding a new city to the Realtor.com scraper.

Ask the user: "Which city do you want to add? Provide the city name and state (e.g. Las Vegas, NV)"

Then:

1. Find the _CITIES list in scrapers/realtor_scraper.py
2. Show the current cities already in the list
3. Determine the correct Realtor.com slug format for the new city:
   - Format is CityName_ST (e.g. Las_Vegas_NV, Chicago_IL)
   - Spaces in city names become underscores
4. Add the new entry to _CITIES and _CITY_SLUGS
5. Confirm the change and remind the user to test with scraper_max_pages=1 first before a full run

Also check: if the city name contains special characters or multiple words, handle them correctly in the slug.
