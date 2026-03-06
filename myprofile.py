import datetime
from dateutil import relativedelta
import requests
import os
from lxml import etree
import time
import hashlib

HEADERS    = {'authorization': 'token ' + os.environ['ACCESS_TOKEN']}
USER_NAME  = os.environ['USER_NAME']   # 'dayzzaja'
BIRTHDAY   = datetime.datetime(2008, 4, 7)
QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0,
               'recursive_loc': 0, 'graph_commits': 0, 'loc_query': 0}


# ── BIRTHDAY & AGE ────────────────────────────────────────────────────────────

def is_birthday():
    today = datetime.date.today()
    return today.month == BIRTHDAY.month and today.day == BIRTHDAY.day


def daily_readme(birthday):
    """
    Returns a human-readable age string.
    e.g. '17 years, 2 months, 5 days'
    On birthday: '17 years, 0 months, 0 days 🎂'
    """
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    cake = ' 🎂' if (diff.months == 0 and diff.days == 0) else ''
    return '{} {}, {} {}, {} {}{}'.format(
        diff.years,  'year'  + ('s' if diff.years  != 1 else ''),
        diff.months, 'month' + ('s' if diff.months != 1 else ''),
        diff.days,   'day'   + ('s' if diff.days   != 1 else ''),
        cake)


# ── GITHUB API HELPERS ────────────────────────────────────────────────────────

def simple_request(func_name, query, variables):
    request = requests.post('https://api.github.com/graphql',
                            json={'query': query, 'variables': variables},
                            headers=HEADERS)
    if request.status_code == 200:
        return request
    raise Exception(func_name, 'failed with', request.status_code, request.text)


def graph_repos_stars(count_type, owner_affiliation, cursor=None):
    query_count('graph_repos_stars')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazers { totalCount }
                        }
                    }
                }
                pageInfo { endCursor hasNextPage }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(graph_repos_stars.__name__, query, variables)
    if count_type == 'repos':
        return request.json()['data']['user']['repositories']['totalCount']
    elif count_type == 'stars':
        return stars_counter(request.json()['data']['user']['repositories']['edges'])


def recursive_loc(owner, repo_name, data, cache_comment,
                  addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    query_count('recursive_loc')
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit { committedDate }
                                    author { user { id } }
                                    deletions
                                    additions
                                }
                            }
                            pageInfo { endCursor hasNextPage }
                        }
                    }
                }
            }
        }
    }'''
    variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
    request = requests.post('https://api.github.com/graphql',
                            json={'query': query, 'variables': variables},
                            headers=HEADERS)
    if request.status_code == 200:
        if request.json()['data']['repository']['defaultBranchRef'] is not None:
            return loc_counter_one_repo(owner, repo_name, data, cache_comment,
                                        request.json()['data']['repository']['defaultBranchRef']['target']['history'],
                                        addition_total, deletion_total, my_commits)
        else:
            return 0
    force_close_file(data, cache_comment)
    if request.status_code == 403:
        raise Exception('Too many requests — hit anti-abuse limit!')
    raise Exception('recursive_loc() failed with', request.status_code, request.text)


def loc_counter_one_repo(owner, repo_name, data, cache_comment,
                          history, addition_total, deletion_total, my_commits):
    for node in history['edges']:
        if node['node']['author']['user'] == OWNER_ID:
            my_commits     += 1
            addition_total += node['node']['additions']
            deletion_total += node['node']['deletions']
    if not history['edges'] or not history['pageInfo']['hasNextPage']:
        return addition_total, deletion_total, my_commits
    return recursive_loc(owner, repo_name, data, cache_comment,
                         addition_total, deletion_total, my_commits,
                         history['pageInfo']['endCursor'])


def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=[]):
    query_count('loc_query')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            defaultBranchRef {
                                target {
                                    ... on Commit { history { totalCount } }
                                }
                            }
                        }
                    }
                }
                pageInfo { endCursor hasNextPage }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(loc_query.__name__, query, variables)
    if request.json()['data']['user']['repositories']['pageInfo']['hasNextPage']:
        edges += request.json()['data']['user']['repositories']['edges']
        return loc_query(owner_affiliation, comment_size, force_cache,
                         request.json()['data']['user']['repositories']['pageInfo']['endCursor'], edges)
    else:
        clean_edges = [e for e in edges if e and e.get('node')]
        return cache_builder(clean_edges, comment_size, force_cache)


def cache_builder(edges, comment_size, force_cache, loc_add=0, loc_del=0):
    cached   = True
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode()).hexdigest() + '.txt'
    try:
        with open(filename, 'r') as f:
            data = f.readlines()
    except FileNotFoundError:
        data = ['This line is a comment block.\n'] * comment_size if comment_size else []
        with open(filename, 'w') as f:
            f.writelines(data)

    if len(data) - comment_size != len(edges) or force_cache:
        cached = False
        flush_cache(edges, filename, comment_size)
        with open(filename, 'r') as f:
            data = f.readlines()

    cache_comment = data[:comment_size]
    data          = data[comment_size:]
    for index in range(len(edges)):
        repo_hash, commit_count, *__ = data[index].split()
        if repo_hash == hashlib.sha256(edges[index]['node']['nameWithOwner'].encode()).hexdigest():
            try:
                if int(commit_count) != edges[index]['node']['defaultBranchRef']['target']['history']['totalCount']:
                    owner, repo_name = edges[index]['node']['nameWithOwner'].split('/')
                    loc = recursive_loc(owner, repo_name, data, cache_comment)
                    data[index] = (repo_hash + ' '
                                   + str(edges[index]['node']['defaultBranchRef']['target']['history']['totalCount'])
                                   + ' ' + str(loc[2]) + ' ' + str(loc[0]) + ' ' + str(loc[1]) + '\n')
            except TypeError:
                data[index] = repo_hash + ' 0 0 0 0\n'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    for line in data:
        loc = line.split()
        loc_add += int(loc[3])
        loc_del += int(loc[4])
    return [loc_add, loc_del, loc_add - loc_del, cached]


def flush_cache(edges, filename, comment_size):
    with open(filename, 'r') as f:
        data = f.readlines()[:comment_size] if comment_size else []
    with open(filename, 'w') as f:
        f.writelines(data)
        for node in edges:
            if node and node.get('node'):
                f.write(hashlib.sha256(node['node']['nameWithOwner'].encode()).hexdigest() + ' 0 0 0 0\n')


def force_close_file(data, cache_comment):
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode()).hexdigest() + '.txt'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    print('Error while writing cache. Partial data saved to', filename)


def stars_counter(data):
    return sum(node['node']['stargazers']['totalCount'] for node in data)


def commit_counter(comment_size):
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode()).hexdigest() + '.txt'
    with open(filename, 'r') as f:
        data = f.readlines()[comment_size:]
    return sum(int(line.split()[2]) for line in data)


def user_getter(username):
    query_count('user_getter')
    query = '''
    query($login: String!){
        user(login: $login) { id createdAt }
    }'''
    request = simple_request(user_getter.__name__, query, {'login': username})
    return {'id': request.json()['data']['user']['id']}, request.json()['data']['user']['createdAt']


def follower_getter(username):
    query_count('follower_getter')
    query = '''
    query($login: String!){
        user(login: $login) { followers { totalCount } }
    }'''
    request = simple_request(follower_getter.__name__, query, {'login': username})
    return int(request.json()['data']['user']['followers']['totalCount'])


def query_count(funct_id):
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def perf_counter(funct, *args):
    start = time.perf_counter()
    result = funct(*args)
    return result, time.perf_counter() - start


# ── SVG WRITER ────────────────────────────────────────────────────────────────

def svg_overwrite(filename, age_data, commit_data, star_data, repo_data,
                  contrib_data, follower_data, loc_data, birthday_today):
    tree = etree.parse(filename)
    root = tree.getroot()

    find_and_replace(root, 'age_data',      age_data)
    find_and_replace(root, 'commit_data',   '{:,}'.format(commit_data))
    find_and_replace(root, 'star_data',     '{:,}'.format(star_data))
    find_and_replace(root, 'repo_data',     '{:,}'.format(repo_data))
    find_and_replace(root, 'contrib_data',  '{:,}'.format(contrib_data))
    find_and_replace(root, 'follower_data', '{:,}'.format(follower_data))
    find_and_replace(root, 'loc_data',      '{:,}'.format(loc_data[2]))
    find_and_replace(root, 'loc_add',       '{:,}'.format(loc_data[0]))
    find_and_replace(root, 'loc_del',       '{:,}'.format(loc_data[1]))

    # 🎂 Birthday effect
    if birthday_today:
        find_and_replace(root, 'birthday_msg', '🎂 Happy Birthday, dayzzaja! 🎂')
        # Show confetti by adding class="confetti show" to confetti group
        ns = {'svg': 'http://www.w3.org/2000/svg'}
        confetti = root.find('.//*[@id="confetti_group"]')
        if confetti is not None:
            confetti.set('class', 'confetti show')
    else:
        find_and_replace(root, 'birthday_msg', '')

    tree.write(filename, encoding='utf-8', xml_declaration=True)


def find_and_replace(root, element_id, new_text):
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = new_text


# ── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print('Fetching GitHub stats for:', USER_NAME)
    print('─' * 42)

    user_data, user_time  = perf_counter(user_getter, USER_NAME)
    OWNER_ID, acc_date    = user_data
    print(f'  account data     : {user_time*1000:.1f} ms')

    age_data,  age_time   = perf_counter(daily_readme, BIRTHDAY)
    print(f'  age calculation  : {age_time*1000:.2f} ms  →  {age_data}')

    total_loc, loc_time   = perf_counter(loc_query, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'], 7)
    print(f'  LOC {"(cached)" if total_loc[-1] else "(no cache)"}  : {loc_time:.2f} s')

    commit_data, commit_t = perf_counter(commit_counter, 7)
    star_data,   star_t   = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    repo_data,   repo_t   = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    contrib_data,cont_t   = perf_counter(graph_repos_stars, 'repos', ['OWNER','COLLABORATOR','ORGANIZATION_MEMBER'])
    follower_data,fol_t   = perf_counter(follower_getter, USER_NAME)

    birthday_today = is_birthday()
    print('─' * 42)
    print(f'  commits          : {commit_data}')
    print(f'  stars            : {star_data}')
    print(f'  repos            : {repo_data}')
    print(f'  contributed to   : {contrib_data}')
    print(f'  followers        : {follower_data}')
    print(f'  LOC added        : {total_loc[0]:,}')
    print(f'  LOC deleted      : {total_loc[1]:,}')
    print(f'  LOC net          : {total_loc[2]:,}')
    print(f'  🎂 birthday today : {birthday_today}')
    print('─' * 42)

    svg_overwrite('dark_mode.svg',  age_data, commit_data, star_data, repo_data,
                  contrib_data, follower_data, total_loc[:-1], birthday_today)
    svg_overwrite('light_mode.svg', age_data, commit_data, star_data, repo_data,
                  contrib_data, follower_data, total_loc[:-1], birthday_today)

    print('SVGs updated! ✅')
    print(f'Total API calls: {sum(QUERY_COUNT.values())}')
