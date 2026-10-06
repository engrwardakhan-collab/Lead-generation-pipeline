# Pipeline Status

Query the Supabase leads table and show a full status breakdown of the pipeline.

Using the Supabase MCP tools, run the following and display results in a clean table:

1. Count leads by status: new, enriched, personalized, contacted, replied, interested, unsubscribed
2. Count leads by source (realtor, zillow, etc.)
3. Count leads by city/location (top 5)
4. Show total leads in DB
5. Show how many leads have email vs no email
6. Show the 5 most recent "interested" leads with name, email, brokerage, location

Format the output clearly with headers and totals. Flag anything that looks wrong (e.g. 0 enriched leads, large error counts).
