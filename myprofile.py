import datetime, os, base64, hashlib, time, requests
from dateutil import relativedelta
from lxml import etree

# ── AUTH ──────────────────────────────────────────────────────────────────────
_token  = os.environ['ACCESS_TOKEN']
HEADERS = {'authorization': ('Bearer ' if _token.startswith('github_pat_') else 'token ') + _token}
USER    = os.environ['USER_NAME']
BDAY    = datetime.datetime(2008, 4, 7)
OWNER_ID = ''

DAY_IMG = {0:'images/mon.jpg',1:'images/tue.jpg',2:'images/wed.jpg',
           3:'images/thu.jpg',4:'images/fri.jpg',5:'images/sat.jpg',6:'images/sun.jpg'}

# ── HELPERS ───────────────────────────────────────────────────────────────────
def gql(q, v=None):
    r = requests.post('https://api.github.com/graphql',
                      json={'query': q, 'variables': v or {}}, headers=HEADERS)
    if r.status_code == 200: return r.json()
    raise Exception(f'GQL {r.status_code}: {r.text[:200]}')

def rest(path, params=None):
    return requests.get(f'https://api.github.com{path}', headers=HEADERS, params=params or {})

def b64img(path):
    with open(path,'rb') as f: return base64.b64encode(f.read()).decode()

# ── FETCH ALL STATS ───────────────────────────────────────────────────────────
def get_owner_id():
    return gql('query($l:String!){user(login:$l){id}}',{'l':USER})['data']['user']['id']

def get_age():
    diff = relativedelta.relativedelta(datetime.datetime.today(), BDAY)
    cake = ' 🎂' if diff.months==0 and diff.days==0 else ''
    return '{} {}, {} {}, {} {}{}'.format(
        diff.years,  'year' +('s' if diff.years !=1 else ''),
        diff.months, 'month'+('s' if diff.months!=1 else ''),
        diff.days,   'day'  +('s' if diff.days  !=1 else ''), cake)

def get_repos():
    # REST API — includes ALL private repos
    total, page = 0, 1
    while True:
        r = rest('/user/repos', {'type':'owner','per_page':100,'page':page})
        if r.status_code != 200: break
        d = r.json()
        if not d: break
        total += len(d)
        if len(d) < 100: break
        page += 1
    return total

def get_stars():
    total, cursor = 0, None
    while True:
        d = gql('''query($l:String!,$c:String){user(login:$l){
            repositories(first:100,after:$c,ownerAffiliations:OWNER){
                edges{node{stargazers{totalCount}}}
                pageInfo{endCursor hasNextPage}}}}''', {'l':USER,'c':cursor})
        repos = d['data']['user']['repositories']
        total += sum(e['node']['stargazers']['totalCount'] for e in repos['edges'])
        if not repos['pageInfo']['hasNextPage']: break
        cursor = repos['pageInfo']['endCursor']
    return total

def get_commits():
    d = gql('''query($l:String!){user(login:$l){contributionsCollection{
        totalCommitContributions restrictedContributionsCount}}}''',{'l':USER})
    c = d['data']['user']['contributionsCollection']
    return c['totalCommitContributions'] + c['restrictedContributionsCount']

def get_contributions():
    d = gql('''query($l:String!){user(login:$l){contributionsCollection{
        totalCommitContributions totalPullRequestContributions
        totalIssueContributions totalPullRequestReviewContributions
        totalRepositoryContributions restrictedContributionsCount}}}''',{'l':USER})
    return sum(d['data']['user']['contributionsCollection'].values())

def get_followers():
    d = gql('query($l:String!){user(login:$l){followers{totalCount}}}',{'l':USER})
    return d['data']['user']['followers']['totalCount']

def get_profile_b64():
    r = requests.get(f'https://avatars.githubusercontent.com/{USER}?size=80')
    return base64.b64encode(r.content).decode() if r.status_code==200 else ''

# ── LOC ───────────────────────────────────────────────────────────────────────
def get_all_edges():
    edges, cursor = [], None
    while True:
        d = gql('''query($l:String!,$c:String){user(login:$l){
            repositories(first:60,after:$c,
                ownerAffiliations:[OWNER,COLLABORATOR,ORGANIZATION_MEMBER]){
                edges{node{nameWithOwner
                    defaultBranchRef{target{...on Commit{history{totalCount}}}}}}
                pageInfo{endCursor hasNextPage}}}}''', {'l':USER,'c':cursor})
        page = d['data']['user']['repositories']
        edges += [e for e in page['edges'] if e and e.get('node')]
        if not page['pageInfo']['hasNextPage']: break
        cursor = page['pageInfo']['endCursor']
    return edges

def loc_for_repo(owner, repo, cursor=None):
    global OWNER_ID
    add = dele = 0
    r = requests.post('https://api.github.com/graphql', headers=HEADERS,
        json={'query':'''query($o:String!,$r:String!,$c:String){repository(owner:$o,name:$r){
            defaultBranchRef{target{...on Commit{history(first:100,after:$c){
                edges{node{author{user{id}} additions deletions}}
                pageInfo{endCursor hasNextPage}}}}}}}''',
              'variables':{'o':owner,'r':repo,'c':cursor}})
    if r.status_code != 200: return 0, 0
    repo_d = r.json().get('data',{}).get('repository')
    if not repo_d or not repo_d.get('defaultBranchRef'): return 0, 0
    hist = repo_d['defaultBranchRef']['target']['history']
    for e in hist['edges']:
        u = e['node']['author']['user']
        if u and u['id'] == OWNER_ID:
            add  += e['node']['additions']
            dele += e['node']['deletions']
    if hist['pageInfo']['hasNextPage']:
        a2,d2 = loc_for_repo(owner, repo, hist['pageInfo']['endCursor'])
        add+=a2; dele+=d2
    return add, dele

def get_loc():
    cache_file = 'cache/'+hashlib.sha256(USER.encode()).hexdigest()+'.txt'
    cache = {}
    try:
        for line in open(cache_file):
            p = line.strip().split()
            if len(p)==4: cache[p[0]]=[int(p[1]),int(p[2]),int(p[3])]
    except FileNotFoundError:
        pass

    edges = get_all_edges()
    new_cache = {}
    total_add = total_del = 0

    for edge in edges:
        node = edge['node']
        h = hashlib.sha256(node['nameWithOwner'].encode()).hexdigest()
        try: cc = node['defaultBranchRef']['target']['history']['totalCount']
        except: cc = 0

        if h in cache and cache[h][0] == cc:
            add, dele = cache[h][1], cache[h][2]
        else:
            owner, repo = node['nameWithOwner'].split('/')
            add, dele = loc_for_repo(owner, repo)
            print(f'    LOC {node["nameWithOwner"]}: +{add} -{dele}')

        new_cache[h] = [cc, add, dele]
        total_add += add
        total_del += dele

    with open(cache_file,'w') as f:
        for h,v in new_cache.items():
            f.write(f'{h} {v[0]} {v[1]} {v[2]}\n')

    return total_add, total_del, total_add - total_del

# ── SVG BUILDER ───────────────────────────────────────────────────────────────
def make_svg(theme, age, commits, stars, repos, contribs, followers,
             loc, birthday, avatar_b64, profile_b64):
    dk = theme == 'dark'
    card   = '#161b22' if dk else '#ffffff'
    border = '#30363d' if dk else '#d0d7de'
    title  = '#e6edf3' if dk else '#1f2328'
    sub    = '#8b949e' if dk else '#636c76'
    lbl    = '#c9d1d9' if dk else '#444c56'
    acc    = '#a78bfa' if dk else '#6d28d9'
    div    = '#21262d' if dk else '#d8dee4'
    sec    = '#484f58' if dk else '#9198a1'
    lnk    = '#58a6ff' if dk else '#0969da'

    AX,AY,AS = 434,24,220
    CY = AY+AS+14
    TKX,IGX = AX, AX+117
    MQY = CY+68
    MQX = AX
    MQW = 234

    # Profile avatar circle
    PCR = 20
    PCX = 24+PCR
    PCY = 14+PCR
    TX  = 24+PCR*2+10

    mq_text = '👋 Halo semuanya! Selamat datang.'
    bday_txt = '🎂 Happy Birthday, dayzzaja! 🎂' if birthday else ''
    confetti_cls = 'confetti show' if birthday else 'confetti'

    profile_src = f'data:image/jpeg;base64,{profile_b64}' if profile_b64 else ''
    avatar_src  = f'data:image/jpeg;base64,{avatar_b64}'

    svg = f'''<?xml version='1.0' encoding='utf-8'?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="680" height="390">
<defs>
  <clipPath id="ac"><rect x="{AX}" y="{AY}" width="{AS}" height="{AS}" rx="14"/></clipPath>
  <clipPath id="pc"><circle cx="{PCX}" cy="{PCY}" r="{PCR}"/></clipPath>
  <clipPath id="mc"><rect x="{MQX}" y="{MQY-16}" width="{MQW}" height="22"/></clipPath>
  <linearGradient id="ig" x1="0%" y1="100%" x2="100%" y2="0%">
    <stop offset="0%"   stop-color="#f09433"/>
    <stop offset="50%"  stop-color="#dc2743"/>
    <stop offset="100%" stop-color="#bc1888"/>
  </linearGradient>
  <filter id="sh"><feDropShadow dx="0" dy="3" stdDeviation="6" flood-color="#7c3aed" flood-opacity="0.35"/></filter>
  <filter id="pg"><feDropShadow dx="0" dy="0" stdDeviation="3" flood-color="#7c3aed" flood-opacity="0.6"/></filter>
  <style>
    @keyframes fall{{0%{{transform:translateY(0) rotate(0deg);opacity:1}}100%{{transform:translateY(370px) rotate(540deg);opacity:0}}}}
    @keyframes bp{{0%,100%{{opacity:1}}50%{{opacity:0.4}}}}
    @keyframes mq{{0%{{transform:translateX({AX+MQW}px)}}100%{{transform:translateX(-320px)}}}}
    .c1{{animation:fall 3.0s 0.0s linear infinite}}
    .c2{{animation:fall 2.7s 0.5s linear infinite}}
    .c3{{animation:fall 3.4s 1.0s linear infinite}}
    .c4{{animation:fall 2.5s 0.3s linear infinite}}
    .c5{{animation:fall 3.1s 0.8s linear infinite}}
    .c6{{animation:fall 2.8s 1.3s linear infinite}}
    .bp{{animation:bp 1.4s ease-in-out infinite}}
    .confetti{{display:none}}.confetti.show{{display:block}}
    .mq{{animation:mq 18s linear infinite}}
  </style>
</defs>

<!-- Card -->
<rect width="680" height="390" rx="16" fill="{card}"/>
<rect width="680" height="390" rx="16" fill="none" stroke="{border}" stroke-width="1.5"/>
<rect x="0" y="0" width="5" height="390" rx="3" fill="#7c3aed"/>

<!-- Confetti -->
<g class="{confetti_cls}">
  <rect class="c1" x="40"  y="0" width="8" height="8"  rx="2" fill="#f472b6"/>
  <rect class="c2" x="100" y="0" width="7" height="10" rx="1" fill="#818cf8"/>
  <rect class="c3" x="170" y="0" width="9" height="7"  rx="2" fill="#34d399"/>
  <rect class="c4" x="240" y="0" width="8" height="8"  rx="1" fill="#fbbf24"/>
  <rect class="c5" x="310" y="0" width="7" height="9"  rx="2" fill="#f472b6"/>
  <rect class="c6" x="380" y="0" width="9" height="7"  rx="1" fill="#60a5fa"/>
</g>

<!-- Daily avatar -->
<rect x="{AX-2}" y="{AY-2}" width="{AS+4}" height="{AS+4}" rx="16" fill="#7c3aed" opacity="0.15" filter="url(#sh)"/>
<rect x="{AX-2}" y="{AY-2}" width="{AS+4}" height="{AS+4}" rx="16" fill="none" stroke="#7c3aed" stroke-width="1.5" opacity="0.6"/>
<image href="{avatar_src}" x="{AX}" y="{AY}" width="{AS}" height="{AS}" clip-path="url(#ac)" preserveAspectRatio="xMidYMid slice"/>

<!-- Profile circle -->
<circle cx="{PCX}" cy="{PCY}" r="{PCR+2}" fill="#7c3aed" opacity="0.3" filter="url(#pg)"/>
<circle cx="{PCX}" cy="{PCY}" r="{PCR+1.5}" fill="none" stroke="#7c3aed" stroke-width="1.5" opacity="0.8"/>
{f'<image href="{profile_src}" x="{24}" y="{14}" width="{PCR*2}" height="{PCR*2}" clip-path="url(#pc)" preserveAspectRatio="xMidYMid slice"/>' if profile_src else ''}

<!-- Header -->
<text x="{TX}" y="43" font-family="Georgia,serif" font-size="24" font-weight="700" font-style="italic" fill="{title}">Dayzzaja</text>
<text x="{TX}" y="62" font-family="Fira Code,monospace" font-size="10.5" fill="{sub}">@dayzzaja · when yeah :D · slow to respond 🐢</text>
<text x="24" y="88" font-family="Segoe UI,sans-serif" font-size="13" font-weight="700" fill="#f472b6" class="bp">{bday_txt}</text>
<line x1="24" y1="98" x2="415" y2="98" stroke="{div}" stroke-width="1"/>

<!-- About -->
<text x="24" y="116" font-family="Fira Code,monospace" font-size="9" font-weight="600" letter-spacing="1.5" fill="{sec}">── ABOUT ──────────────────────────────</text>
<text x="24"  y="136" font-family="Fira Code,monospace" font-size="13" fill="{lbl}">Age</text>
<text x="405" y="136" font-family="Fira Code,monospace" font-size="13" font-weight="700" fill="{acc}" text-anchor="end">{age}</text>
<line x1="24" y1="148" x2="415" y2="148" stroke="{div}" stroke-width="1"/>

<!-- GitHub -->
<text x="24" y="165" font-family="Fira Code,monospace" font-size="9" font-weight="600" letter-spacing="1.5" fill="{sec}">── GITHUB ─────────────────────────────</text>
<text x="24"  y="186" font-family="Fira Code,monospace" font-size="13" fill="{lbl}">Commits</text>
<text x="405" y="186" font-family="Fira Code,monospace" font-size="13" font-weight="700" fill="{acc}" text-anchor="end">{commits:,}</text>
<text x="24"  y="207" font-family="Fira Code,monospace" font-size="13" fill="{lbl}">Stars Earned</text>
<text x="405" y="207" font-family="Fira Code,monospace" font-size="13" font-weight="700" fill="{acc}" text-anchor="end">{stars:,}</text>
<text x="24"  y="228" font-family="Fira Code,monospace" font-size="13" fill="{lbl}">Repositories</text>
<text x="405" y="228" font-family="Fira Code,monospace" font-size="13" font-weight="700" fill="{acc}" text-anchor="end">{repos:,}</text>
<text x="24"  y="249" font-family="Fira Code,monospace" font-size="13" fill="{lbl}">Contributions</text>
<text x="405" y="249" font-family="Fira Code,monospace" font-size="13" font-weight="700" fill="{acc}" text-anchor="end">{contribs:,}</text>
<text x="24"  y="270" font-family="Fira Code,monospace" font-size="13" fill="{lbl}">Followers</text>
<text x="405" y="270" font-family="Fira Code,monospace" font-size="13" font-weight="700" fill="{acc}" text-anchor="end">{followers:,}</text>
<line x1="24" y1="282" x2="415" y2="282" stroke="{div}" stroke-width="1"/>

<!-- LOC -->
<text x="24" y="298" font-family="Fira Code,monospace" font-size="9" font-weight="600" letter-spacing="1.5" fill="{sec}">── LINES OF CODE ON GITHUB ────────────</text>
<text x="24"  y="318" font-family="Fira Code,monospace" font-size="13" fill="{lbl}">Added</text>
<text x="100" y="318" font-family="Fira Code,monospace" font-size="13" font-weight="700" fill="{acc}">{loc[0]:,}</text>
<text x="170" y="318" font-family="Fira Code,monospace" font-size="13" fill="{lbl}">Deleted</text>
<text x="256" y="318" font-family="Fira Code,monospace" font-size="13" font-weight="700" fill="{acc}">{loc[1]:,}</text>
<text x="326" y="318" font-family="Fira Code,monospace" font-size="13" fill="{lbl}">Net</text>
<text x="366" y="318" font-family="Fira Code,monospace" font-size="13" font-weight="700" fill="{acc}">{loc[2]:,}</text>

<!-- Contact -->
<text x="{AX}" y="{CY+14}" font-family="Fira Code,monospace" font-size="9" font-weight="600" letter-spacing="1.5" fill="{sec}">── CONTACT ──────</text>
<!-- TikTok -->
<g transform="translate({TKX},{CY+20})">
  <rect width="20" height="20" rx="5" fill="#010101"/>
  <g transform="translate(3,2) scale(0.58)">
    <path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.1V9.01a6.33 6.33 0 00-.79-.05 6.34 6.34 0 00-6.34 6.34 6.34 6.34 0 006.34 6.34 6.34 6.34 0 006.33-6.34V8.95a8.16 8.16 0 004.77 1.52V7.01a4.85 4.85 0 01-1-.32z" fill="white"/>
  </g>
</g>
<text x="{TKX+26}" y="{CY+35}" font-family="Fira Code,monospace" font-size="11" fill="{lnk}">@_dzzwkwk</text>
<!-- Instagram -->
<g transform="translate({IGX},{CY+20})">
  <rect width="20" height="20" rx="6" fill="url(#ig)"/>
  <g transform="translate(3.5,3.5) scale(0.65)">
    <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z" fill="white"/>
  </g>
</g>
<text x="{IGX+26}" y="{CY+35}" font-family="Fira Code,monospace" font-size="11" fill="{lnk}">@dzz_hehehe</text>

<!-- Marquee -->
<line x1="{MQX}" y1="{MQY-18}" x2="{MQX+MQW}" y2="{MQY-18}" stroke="{div}" stroke-width="1"/>
<g clip-path="url(#mc)">
  <text class="mq" x="0" y="{MQY}" font-family="Segoe UI,sans-serif" font-size="11" fill="{acc}" opacity="0.9">{mq_text}</text>
</g>
</svg>'''
    return svg

# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print(f'Stats for: {USER}')
    print('─'*42)

    OWNER_ID = get_owner_id()
    print(f'  owner_id     : {OWNER_ID}')

    age      = get_age()
    loc      = get_loc()
    commits  = get_commits()
    stars    = get_stars()
    repos    = get_repos()
    contribs = get_contributions()
    followers= get_followers()
    birthday = (datetime.date.today().month == BDAY.month and
                datetime.date.today().day   == BDAY.day)

    print(f'  age          : {age}')
    print(f'  loc          : +{loc[0]:,} / -{loc[1]:,} / net {loc[2]:,}')
    print(f'  commits      : {commits}')
    print(f'  stars        : {stars}')
    print(f'  repos        : {repos}')
    print(f'  contributions: {contribs}')
    print(f'  followers    : {followers}')

    avatar  = b64img(DAY_IMG[datetime.date.today().weekday()])
    profile = get_profile_b64()
    print(f'  profile pic  : {"OK ✅" if profile else "FAILED ❌"}')
    print('─'*42)

    for theme in ['dark','light']:
        svg = make_svg(theme, age, commits, stars, repos, contribs, followers,
                       loc, birthday, avatar, profile)
        fname = f'{theme}_mode.svg'
        with open(fname,'w',encoding='utf-8') as f:
            f.write(svg)
        print(f'  {fname} written ✅')

    print('Done!')
