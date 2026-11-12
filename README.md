# Tech Daily

A one-page tech news site that rebuilds itself every day. Each story shows its
picture, headline, a short summary and a link to the original article.

## Put it online (free, about 5 minutes)

1. Create a new GitHub repository and upload everything in this folder
   (including the hidden `.github` folder).
2. Repo **Settings > Pages > Build and deployment > Source**: choose **GitHub Actions**.
3. Open the **Actions** tab, pick **Update Tech Daily**, press **Run workflow**.
4. Your site appears at `https://<your-username>.github.io/<repo-name>/`.

After that it refreshes by itself at 06:00 and 18:00 Pakistan time.
Change the times in `.github/workflows/daily.yml` (cron is in UTC).

## Try it on your computer

    pip install -r requirements.txt
    python fetch_news.py
    open site/index.html

## Customize

- Add or remove sites: edit `SOURCES` in `fetch_news.py` (any RSS/Atom feed works).
- "Today" window, stories per site, total stories: constants at the top of `fetch_news.py`.
- Topics are assigned by keywords in `CATEGORIES`.
- Look and feel: `template.html`.

If a site changes its feed address, the run still succeeds and the footer of the
build log lists which feeds failed.
