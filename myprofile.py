import datetime
from dateutil import relativedelta
import requests
import os
import base64
from lxml import etree
import time
import hashlib

_token    = os.environ['ACCESS_TOKEN']
HEADERS   = {'authorization': ('Bearer ' if _token.startswith('github_pat_') else 'token ') + _token}
USER_NAME = os.environ['USER_NAME']
BIRTHDAY  = datetime.datetime(2008, 4, 7)

# OWNER_ID is fetched from GitHub and stored here so all functions can use it
OWNER_ID  = ''

DAY_IMAGES = {
    0: 'images/mon.jpg',
    1: 'images/tue.jpg',
    2: 'images/wed.jpg',
    3: 'images/thu.jpg',
    4: 'images/fri.jpg',
    5: 'images/sat.jpg',
    6: 'images/sun.jpg',
}

# ── AVATARS ───────────────────────────────────────────────────────────────────

def get_profile_avatar_b64():
    r = requests.get(f'https://avatars.githubusercontent.com/{USER_NAME}?size=80')
    return base64.b64encode(r.content).decode('utf-8') if r.status_code == 200 else ''

def get_daily_avatar_b64():
    with open(DAY_IMAGES[datetime.date.today().weekday()], 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')

# ── AGE & BIRTHDAY ────────────────────────────────────────────────────────────

def is_birthday():
    t = datetime.date.today()
    return t.month == BIRTHDAY.month and t.day == BIRTHDAY.day

def daily_readme(birthday):
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    cake = ' 🎂' if (diff.months == 0 and diff.days == 0) else ''
    return '{} {}, {} {}, {} {}{}'.format(
        diff.years,  'year'  + ('s' if diff.years  != 1 else ''),
        diff.months, 'month' + ('s' if diff.months != 1 else ''),
        diff.days,   'day'   + ('s' if diff.days   != 1 else ''),
        cake)

# ── API HELPERS ───────────────────────────────────────────────────────────────

def gql(query, variables=None):
    r = requests.post('https://api.github.com/graphql',
                      json={'query': query, 'variables': variables or {}},
                      headers=HEADERS)
    if r.status_code == 200:
        return r.json()
    raise Exception(f'GraphQL failed {r.status_code}: {r.text}')

def rest_get(path, params=None):
    r = requests.get(f'https://api.github.com{path}', headers=HEADERS, params=params or {})
    return r

# ── USER ──────────────────────────────────────────────────────────────────────

def get_owner_id():
    data = gql('query($l:String!){user(login:$l){id}}', {'l': USER_NAME})
    return data['data']['user']['id']

def get_followers():
    data = gql('query($l:String!){user(login:$l){followers{totalCount}}}', {'l': USER_NAME})
    return data['data']['user']['followers']['totalCount']

# ── REPOS & STARS ─────────────────────────────────────────────────────────────

def get_repo_count():
    """REST API — returns ALL repos including private."""
    total, page = 0, 1
    while True:
        r = rest_get('/user/repos', {'type': 'owner', 'per_page': 100, 'page': page})
        if r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        total += len(data)
        if len(data) < 100:
            break
        page += 1
    return total

def get_stars():
    """GraphQL — count stars across all owned repos."""
    total, cursor = 0, None
    while True:
        q = '''query($l:String!,$c:String){user(login:$l){
            repositories(first:100,after:$c,ownerAffiliations:OWNER){
                edges{node{stargazers{totalCount}}}
                pageInfo{endCursor hasNextPage}
            }}}'''
        data = gql(q, {'l': USER_NAME, 'c': cursor})
        repos = data['data']['user']['repositories']
        total += sum(e['node']['stargazers']['totalCount'] for e in repos['edges'])
        if not repos['pageInfo']['hasNextPage']:
            break
        cursor = repos['pageInfo']['endCursor']
    return total

# ── COMMITS & CONTRIBUTIONS ───────────────────────────────────────────────────

def get_commits():
    q = '''query($l:String!){user(login:$l){contributionsCollection{
        totalCommitContributions restrictedContributionsCount}}}'''
    d = gql(q, {'l': USER_NAME})['data']['user']['contributionsCollection']
    return d['totalCommitContributions'] + d['restrictedContributionsCount']

def get_contributions():
    q = '''query($l:String!){user(login:$l){contributionsCollection{
        totalCommitContributions totalPullRequestContributions
        totalIssueContributions totalPullRequestReviewContributions
        totalRepositoryContributions restrictedContributionsCount}}}'''
    d = gql(q, {'l': USER_NAME})['data']['user']['contributionsCollection']
    return sum(d.values())

# ── LINES OF CODE ─────────────────────────────────────────────────────────────

def get_all_repo_edges():
    """Get all repo edges including private via GraphQL."""
    edges, cursor = [], None
    q = '''query($l:String!,$c:String){user(login:$l){
        repositories(first:60,after:$c,ownerAffiliations:[OWNER,COLLABORATOR,ORGANIZATION_MEMBER]){
            edges{node{nameWithOwner
                defaultBranchRef{target{...on Commit{history{totalCount}}}}
            }}
            pageInfo{endCursor hasNextPage}
        }}}'''
    while True:
        data = gql(q, {'l': USER_NAME, 'c': cursor})
        page = data['data']['user']['repositories']
        edges += [e for e in page['edges'] if e and e.get('node')]
        if not page['pageInfo']['hasNextPage']:
            break
        cursor = page['pageInfo']['endCursor']
    return edges

def count_loc_in_repo(owner, repo_name, cursor=None):
    """Recursively count lines added/deleted by OWNER_ID in a repo."""
    global OWNER_ID
    add, delete = 0, 0
    q = '''query($o:String!,$r:String!,$c:String){repository(owner:$o,name:$r){
        defaultBranchRef{target{...on Commit{history(first:100,after:$c){
            edges{node{author{user{id}} additions deletions}}
            pageInfo{endCursor hasNextPage}
        }}}}}}'''
    r = requests.post('https://api.github.com/graphql',
                      json={'query': q, 'variables': {'o': owner, 'r': repo_name, 'c': cursor}},
                      headers=HEADERS)
    if r.status_code != 200:
        return 0, 0
    repo_data = r.json().get('data', {}).get('repository')
    if not repo_data or not repo_data.get('defaultBranchRef'):
        return 0, 0
    history = repo_data['defaultBranchRef']['target']['history']
    for edge in history['edges']:
        node = edge['node']
        if node['author']['user'] and node['author']['user']['id'] == OWNER_ID:
            add    += node['additions']
            delete += node['deletions']
    if history['pageInfo']['hasNextPage']:
        a2, d2 = count_loc_in_repo(owner, repo_name, history['pageInfo']['endCursor'])
        add += a2; delete += d2
    return add, delete

def get_loc():
    """Calculate total LOC, using cache to skip unchanged repos."""
    cache_file = 'cache/' + hashlib.sha256(USER_NAME.encode()).hexdigest() + '.txt'
    # Load cache: {repo_hash: [commit_count, add, delete]}
    cache = {}
    try:
        with open(cache_file) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) == 4:
                    cache[parts[0]] = [int(parts[1]), int(parts[2]), int(parts[3])]
    except FileNotFoundError:
        pass

    edges = get_all_repo_edges()
    new_cache = {}
    total_add = total_del = 0

    for edge in edges:
        node       = edge['node']
        repo_hash  = hashlib.sha256(node['nameWithOwner'].encode()).hexdigest()
        try:
            commit_count = node['defaultBranchRef']['target']['history']['totalCount']
        except (TypeError, KeyError):
            commit_count = 0

        if repo_hash in cache and cache[repo_hash][0] == commit_count:
            # No new commits — use cached value
            _, add, delete = cache[repo_hash]
        else:
            # Repo changed or not cached — recalculate
            owner, repo_name = node['nameWithOwner'].split('/')
            add, delete = count_loc_in_repo(owner, repo_name)
            print(f'    calculated {node["nameWithOwner"]}: +{add} -{delete}')

        new_cache[repo_hash] = [commit_count, add, delete]
        total_add += add
        total_del += delete

    # Save updated cache
    with open(cache_file, 'w') as f:
        for h, vals in new_cache.items():
            f.write(f'{h} {vals[0]} {vals[1]} {vals[2]}\n')

    return total_add, total_del, total_add - total_del

# ── SVG WRITER ────────────────────────────────────────────────────────────────

def find_and_replace(root, eid, text):
    el = root.find(f".//*[@id='{eid}']")
    if el is not None:
        el.text = text

def svg_overwrite(template, output, age, commits, stars, repos, contribs,
                  followers, loc, birthday, avatar_b64, profile_b64):
    with open(template) as f:
        content = f.read()
    content = content.replace('AVATAR_PLACEHOLDER',  avatar_b64)
    content = content.replace('PROFILE_PLACEHOLDER', profile_b64)
    tree = etree.fromstring(content.encode('utf-8'))
    find_and_replace(tree, 'age_data',      age)
    find_and_replace(tree, 'commit_data',   f'{commits:,}')
    find_and_replace(tree, 'star_data',     f'{stars:,}')
    find_and_replace(tree, 'repo_data',     f'{repos:,}')
    find_and_replace(tree, 'contrib_data',  f'{contribs:,}')
    find_and_replace(tree, 'follower_data', f'{followers:,}')
    find_and_replace(tree, 'loc_add',       f'{loc[0]:,}')
    find_and_replace(tree, 'loc_del',       f'{loc[1]:,}')
    find_and_replace(tree, 'loc_data',      f'{loc[2]:,}')
    if birthday:
        find_and_replace(tree, 'birthday_msg', '🎂 Happy Birthday, dayzzaja! 🎂')
        c = tree.find('.//*[@id="confetti_group"]')
        if c is not None: c.set('class', 'confetti show')
    else:
        find_and_replace(tree, 'birthday_msg', '')
    with open(output, 'wb') as f:
        f.write(b"<?xml version='1.0' encoding='utf-8'?>\n")
        f.write(etree.tostring(tree, encoding='utf-8', xml_declaration=False))

# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print(f'Fetching stats for: {USER_NAME}')
    print('─' * 42)

    # Must be set before get_loc() so count_loc_in_repo can use it
    OWNER_ID = get_owner_id()
    print(f'  owner_id    : {OWNER_ID}')

    age      = daily_readme(BIRTHDAY)
    loc      = get_loc()
    commits  = get_commits()
    stars    = get_stars()
    repos    = get_repo_count()
    contribs = get_contributions()
    followers= get_followers()

    print(f'  age         : {age}')
    print(f'  loc         : +{loc[0]:,} / -{loc[1]:,} / net {loc[2]:,}')
    print(f'  commits     : {commits}')
    print(f'  stars       : {stars}')
    print(f'  repos       : {repos}')
    print(f'  contribs    : {contribs}')
    print(f'  followers   : {followers}')

    birthday  = is_birthday()
    avatar    = get_daily_avatar_b64()
    profile   = get_profile_avatar_b64()
    print(f'  profile pic : {"OK ✅" if profile else "FAILED ❌"}')
    print('─' * 42)

    svg_overwrite('dark_mode_template.svg',  'dark_mode.svg',
                  age, commits, stars, repos, contribs, followers, loc, birthday, avatar, profile)
    svg_overwrite('light_mode_template.svg', 'light_mode.svg',
                  age, commits, stars, repos, contribs, followers, loc, birthday, avatar, profile)

    print('SVGs updated ✅')
