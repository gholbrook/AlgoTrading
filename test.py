


def print_graphic_from_url(url):
    import requests
    from bs4 import BeautifulSoup

    response = requests.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')

    coords = {}

    for row in soup.select("table tr"):
        cells = row.find_all(["td", "th"])

        try:
            x = int(cells[0].get_text(strip=True))
            y = int(cells[2].get_text(strip=True))
        except ValueError:
            # skip header values
            continue

        shape = cells[1].get_text()
        coords[(x, y)] = shape

    x_boundary = max(x for x, y in coords)
    y_boundary = max(y for x, y in coords)

    for y in range(y_boundary, -1, -1):
        print("".join(
            coords.get((x, y), " ") for x in range(x_boundary + 1)
        ))


if __name__ == "__main__":
    url = "https://docs.google.com/document/d/e/2PACX-1vSvM5gDlNvt7npYHhp_XfsJvuntUhq184By5xO_pA4b_gCWeXb6dM6ZxwN8rE6S4ghUsCj2VKR21oEP/pub"
    print_graphic_from_url(url)