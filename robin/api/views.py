import logging
import urllib
from datetime import datetime, date

from commons.exceptions import APIError
from django.db.models import F
from members.models import Team, Member
from rest_framework.decorators import api_view
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView
from statistics.models import Repository, Pull, Commit, Comment, ProductBug

from .serializers import (RepositorySerializer,
                          TeamSerializer,
                          PendingSerializer,
                          BasesStatsSerializer,
                          CommentStatsSerializer,
                          MemberSerializer,
                          BugStatsSerializer)

logger = logging.getLogger(__name__)

PRODUCT_BUG_DATA = []


def _paginate_response(data, request):
    paginator = PageNumberPagination()
    result_page = paginator.paginate_queryset(data, request)
    return paginator.get_paginated_response(result_page)


def _stats_type_sortor(stats_type, team_code, kerbroes_id):
    if stats_type == 1:
        # personal stats_type = 1, does not need team code
        # personal takes a string of kerbroes_id, seperated with ',''
        kerbroes_id_list = kerbroes_id.strip().split(',')
    elif stats_type == 2:
        # team stats_type = 2, does not need kerbroes_id
        # team  takes a team_code
        team = Team.objects.get(team_code=team_code)
        members = Member.objects.filter(team=team)
        kerbroes_id_list = [member.kerbroes_id for member in members]
    return kerbroes_id_list


def _build_github_pull_url(owner, repo, pull_number):
    url = 'https://github.com/%s/%s/pull/%s' % (owner, repo, pull_number)
    return url


def _get_merged_by_kerbroes_id(github_account):
    merged_by = github_account
    if merged_by != "null":
        member = Member.objects.filter(github_account=merged_by).first()
        if member:
            merged_by = member.kerbroes_id
    return merged_by


def _bug_status(start_date, end_date, kerbroes_id_list):
    details = {}
    cgi_base_url = ('https://bugzilla.redhat.com/buglist.cgi?columnlist=product'
                    '%2Ccomponent%2Cassigned_to%2Cbug_status%2Cresolution'
                    '%2Cshort_desc%2Cflagtypes.name%2Cqa_contact%2Creporter'
                    '%2Ckeywords%2Cpriority%2Cbug_severity%2Ccf_qa_whiteboard'
                    '%2Cversion')
    robin_list_id = 'ROBIN_LIST_ID'
    robin_role = 'ROBIN_ROLE'
    valid_bz_url = ('&classification=Red%%20Hat&list_id=%s&query_format=advanced'
                    '&f1=keywords&f2=%s&f3=cf_zstream_target_release'
                    '&o1=nowordssubstr&o2=anywordssubstr&o3=isempty' %
                    (robin_list_id, robin_role))
    valid_bz_url += ('&chfield=%%5BBug%%20creation%%5D&chfieldfrom=%s&chfieldto=%s'
                     % (start_date, end_date))

    fields = {
        'bug_status': ['NEW', 'ASSIGNED', 'POST', 'MODIFIED', 'ON_QA', 'VERIFIED', 'CLOSED'],
        'rep_platform': ["Unspecified", "All", "x86_64", "ppc64", "ppc64le",
                         "s390", "s390x", "aarch64", "arm"],
        'component': ['qemu-kvm', 'kernel', 'virtio-win', 'seabios', 'edk2',
                      'slof', 'qemu-guest-agent', 'dtc', 'kernel-rt', 'ovmf',
                      'libtpms', 'virglrenderer', 'qemu-kvm-rhev', 'kernel-rt',
                      'qemu-guest-agent', 'qemu-kvm-ma', 'kernel-alt'],
        'resolution': ["---", "CURRENTRELEASE", "ERRATA"]}

    filters = {'v1': ["ABIAssurance", "TechPreview", "ReleaseNotes", "Tracking",
                     "Task", "HardwareEnablement", "SecurityTracking",
                     "TestOnly", "Improvement", "FutureFeature", "Rebase",
                     "FeatureBackport", "Documentation", "OtherQA", "RFE"],
               'v2': kerbroes_id_list}
    product = {'rhel8': ["Red Hat Enterprise Linux 8",
                         "Red Hat Enterprise Linux Advanced Virtualization"],
               'rhel9': ["Red Hat Enterprise Linux 9"]}

    for key, value in fields.items():
        for op in value:
            valid_bz_url += '&%s=' % key
            valid_bz_url += urllib.quote(op)

    for key, value in filters.items():
        valid_bz_url += '&%s=' % key
        for op in value[:-1]:
            valid_bz_url += urllib.quote('%s,' % op)
        valid_bz_url += urllib.quote(value[-1])

    valid_bz_url += '&api_key=mLPREvS9ArB97djTLlZBmRKeqkp8jDYrCeLX4U58'
    product_names = product.keys()
    product_names.append('all')

    def get_num_and_link(list_id, bz_filter='reporter', high=False):
        product_num = dict.fromkeys(product_names, 0)
        url_list = {}
        url_r = cgi_base_url + valid_bz_url.replace(
            robin_list_id, list_id).replace(robin_role, bz_filter)
        if high:
            url_r += '&priority=urgent&priority=high'
        product_filter_str = ''
        for key, value in product.items():
            url_r_tmp = url_r
            for p_name in value:
                product_filter_tmp = '&product=' + urllib.quote(p_name)
                url_r_tmp += product_filter_tmp
                product_filter_str += product_filter_tmp
                filter_dict = {'bug_product': p_name}
                for kerbroes_id in kerbroes_id_list:
                    filter_dict.update({bz_filter: kerbroes_id})
                    if high:
                        filter_dict.update({'priority': 'high'})
                    bugs = ProductBug.objects.filter(**filter_dict).filter(
                        created_at__range=(start_date, end_date))
                    if high:
                        filter_dict.update({'priority': 'urgent'})
                        bugs = bugs | ProductBug.objects.filter(**filter_dict).filter(
                            created_at__range=(start_date, end_date))
                    if bz_filter == 'reporter':
                        new_count = 0
                        for bug in bugs:
                            if bug.qa_contact in kerbroes_id_list:
                                new_count += 1
                    else:
                        new_count = bugs.count()
                    product_num.update({key: new_count + product_num[key]})
                    product_num.update({'all': product_num['all'] + new_count})
            url_list.update({key: url_r_tmp})
        url_list.update({'all': url_r + product_filter_str})

        return product_num, url_list

    bz_reported_nums, bz_reported_urls = get_num_and_link('11627322', 'reporter')
    bz_qa_contact_nums, bz_qa_contact_urls = get_num_and_link(
        '11627320', 'qa_contact')
    bz_reported_nums_high, bz_reported_urls_high = get_num_and_link(
        '11627322', 'reporter', True)
    bz_qa_contact_nums_high, bz_qa_contact_urls_high = get_num_and_link(
        '11627320', 'qa_contact', True)

    def ratio(num, den):
        valid_bz_ratio = 0
        if den != 0:
            valid_bz_ratio = "%.2f%%" % (float(num)/float(den)*100)
        return valid_bz_ratio

    for product_name in product_names:
        reported_num = bz_reported_nums[product_name]
        qa_contact_num = bz_qa_contact_nums[product_name]
        total_valid_bz_ratio = ratio(reported_num, qa_contact_num)
        reported_num_high = bz_reported_nums_high[product_name]
        qa_contact_num_high = bz_qa_contact_nums_high[product_name]
        high_ratio = ratio(reported_num_high, qa_contact_num_high)
        details.update(
            {'total_valid_bz_ratio_%s' % (product_name): total_valid_bz_ratio,
             'total_reported_%s' % (product_name): reported_num,
             'total_reported_url_%s' % (product_name): bz_reported_urls[product_name],
             'total_qa_contact_%s' % (product_name): qa_contact_num,
             'total_qa_contact_url_%s' % (product_name): bz_qa_contact_urls[product_name],
             'high_ratio_%s' % (product_name): high_ratio,
             'high_reported_%s' % (product_name): reported_num_high,
             'high_reported_url_%s' % (product_name): bz_reported_urls_high[product_name],
             'high_qa_contact_%s' % (product_name): qa_contact_num_high,
             'high_qa_contact_url_%s' % (product_name): bz_qa_contact_urls_high[product_name]
             })

    return details


class CustomPagination(PageNumberPagination):
    def get_paginated_response(self, data):
        return Response({
            'links': {
                'next': self.get_next_link(),
                'previous': self.get_previous_link()
            },
            'count': self.page.paginator.count,
            'results': data
        })


class RepoListView(APIView):
    """
    returns all tracked repositories
    """

    def get(self, request, format=None):
        # Returns a JSON response with a listing of Repository objects
        repositories = Repository.objects.all()
        paginator = PageNumberPagination()
        result_page = paginator.paginate_queryset(repositories, request)
        serializer = RepositorySerializer(result_page, many=True)
        return paginator.get_paginated_response(serializer.data)


class BugListView(APIView):
    """ returns bug status for the whole group"""

    def get(self, request, format=None):
        logger.info('[bug_status] Received data is valid.')
        details = []
        end_date = date.today()
        start_date = date(end_date.year, 1, 1)
        # Whole team data
        members = Member.objects.all()
        kerbroes_id_list = [member.kerbroes_id for member in members]
        d = _bug_status(start_date, end_date, kerbroes_id_list)
        d.update({'team': 'KVM_QE_ALL'})
        details.append(d)
        # Subteam data
        teams = Team.objects.all()
        for team in teams:
            members = Member.objects.filter(team=team)
            kerbroes_id_list = [member.kerbroes_id for member in members]
            d = _bug_status(start_date, end_date, kerbroes_id_list)
            d.update({'team': team.team_name})
            details.append(d)
        PRODUCT_BUG_DATA = details
        response = _paginate_response(details, request)
        return response


class TeamListView(APIView):
    """
    returns all the tracked teams
    """

    def get(self, request, format=None):
        # Returns a JSON response with a listing of Team objects
        teams = Team.objects.all()
        paginator = PageNumberPagination()
        result_page = paginator.paginate_queryset(teams, request)
        serializer = TeamSerializer(result_page, many=True)
        return paginator.get_paginated_response(serializer.data)


@api_view(['GET'])
def member_list(request):
    logger.info('[member_list] Received data : %s' % request.query_params)
    if request.method == 'GET':
        serializer = MemberSerializer(data=request.query_params)
        if serializer.is_valid():
            logger.info('[member_list] Received data is valid.')
            details = []
            team = Team.objects.get(team_code=serializer.validated_data['team_code'])
            members = Member.objects.filter(team=team, serving=True)
            for member in members:
                details.append({'name': member.name,
                                'kerbroes_id': member.kerbroes_id,
                                'github_account': member.github_account,
                                })
            response = _paginate_response(details, request)
            return response

        raise APIError(APIError.INVALID_REQUEST_DATA, detail=serializer.errors)
    raise APIError(APIError.INVALID_REQUEST_METHOD, detail='Does Not Support Post Method')


@api_view(['GET'])
def opening_patchs(request):
    logger.info('[opening_patchs] Received data : %s' % request.query_params)
    if request.method == 'GET':
        serializer = BasesStatsSerializer(data=request.query_params)
        if serializer.is_valid():
            logger.info('[opening_patchs] Received data is valid.')
            start_date = serializer.validated_data['start_date']
            end_date = serializer.validated_data['end_date']

            details = []
            # repo = Repository.objects.get(id=serializer.validated_data['repository_id'])
            repo_id_list = serializer.validated_data['repository_id'].strip().split(',')
            for repo_id in repo_id_list:
                repo = Repository.objects.get(id=repo_id)
                kerbroes_id_list = _stats_type_sortor(serializer.validated_data['stats_type'],
                                                      serializer.validated_data.get('team_code', ''),
                                                      serializer.validated_data.get('kerbroes_id', ''))

                for kerbroes_id in kerbroes_id_list:
                    member = Member.objects.get(kerbroes_id=kerbroes_id)
                    pulls = Pull.objects.filter(repository=repo, author=member.github_account,
                                                created_at__range=(start_date, end_date)).order_by('created_at')
                    for pull in pulls:
                        merged_by = _get_merged_by_kerbroes_id(pull.merged_by)
                        details.append({'patch_number': pull.pull_number,
                                        'repo': repo.repo,
                                        'patch_title': pull.title,
                                        'bug_id': pull.bug_id,
                                        'author': member.kerbroes_id,
                                        'pull_merged': pull.pull_merged,
                                        'commits': pull.commits,
                                        'additions': pull.additions,
                                        'deletions': pull.deletions,
                                        'changed_files': pull.changed_files,
                                        'created_at': pull.created_at,
                                        'updated_at': pull.updated_at,
                                        'closed_at': pull.closed_at,
                                        'merged_by': merged_by,
                                        'patch_url': _build_github_pull_url(repo.owner, repo.repo, pull.pull_number),
                                        })
            response = _paginate_response(details, request)
            return response

        raise APIError(APIError.INVALID_REQUEST_DATA, detail=serializer.errors)
    raise APIError(APIError.INVALID_REQUEST_METHOD, detail='Does Not Support Post Method')


@api_view(['GET'])
def closed_patchs(request):
    logger.info('[closed_patchs] Received data : %s' % request.query_params)
    if request.method == 'GET':
        serializer = BasesStatsSerializer(data=request.query_params)
        if serializer.is_valid():
            logger.info('[closed_patchs] Received data is valid.')
            start_date = serializer.validated_data['start_date']
            end_date = serializer.validated_data['end_date']

            details = []
            # repo = Repository.objects.get(id=serializer.validated_data['repository_id'])
            repo_id_list = serializer.validated_data['repository_id'].strip().split(',')
            for repo_id in repo_id_list:
                repo = Repository.objects.get(id=repo_id)
                kerbroes_id_list = _stats_type_sortor(serializer.validated_data['stats_type'],
                                                      serializer.validated_data.get('team_code', ''),
                                                      serializer.validated_data.get('kerbroes_id', ''))

                for kerbroes_id in kerbroes_id_list:
                    member = Member.objects.get(kerbroes_id=kerbroes_id)
                    pulls = Pull.objects.filter(repository=repo, pull_state=0, pull_merged=True,
                                                author=member.github_account,
                                                closed_at__range=(start_date, end_date))
                    for pull in pulls:
                        merged_by = _get_merged_by_kerbroes_id(pull.merged_by)
                        details.append({'patch_number': pull.pull_number,
                                        'repo': repo.repo,
                                        'patch_title': pull.title,
                                        'bug_id': pull.bug_id,
                                        'author': member.kerbroes_id,
                                        'pull_merged': pull.pull_merged,
                                        'commits': pull.commits,
                                        'additions': pull.additions,
                                        'deletions': pull.deletions,
                                        'changed_files': pull.changed_files,
                                        'created_at': pull.created_at,
                                        'updated_at': pull.updated_at,
                                        'closed_at': pull.closed_at,
                                        'merged_by': merged_by,
                                        'patch_url': _build_github_pull_url(repo.owner, repo.repo, pull.pull_number),
                                        })
            response = _paginate_response(details, request)
            return response

        raise APIError(APIError.INVALID_REQUEST_DATA, detail=serializer.errors)
    raise APIError(APIError.INVALID_REQUEST_METHOD, detail='Does Not Support Post Method')


@api_view(['GET'])
def updated_patchs(request):
    logger.info('[updated_patchs] Received data : %s' % request.query_params)
    if request.method == 'GET':
        serializer = BasesStatsSerializer(data=request.query_params)
        if serializer.is_valid():
            logger.info('[updated_patchs] Received data is valid.')
            start_date = serializer.validated_data['start_date']
            end_date = serializer.validated_data['end_date']

            details = []
            # repo = Repository.objects.get(id=serializer.validated_data['repository_id'])
            repo_id_list = serializer.validated_data['repository_id'].strip().split(',')
            for repo_id in repo_id_list:
                repo = Repository.objects.get(id=repo_id)
                kerbroes_id_list = _stats_type_sortor(serializer.validated_data['stats_type'],
                                                      serializer.validated_data.get('team_code', ''),
                                                      serializer.validated_data.get('kerbroes_id', ''))

                for kerbroes_id in kerbroes_id_list:
                    member = Member.objects.get(kerbroes_id=kerbroes_id)
                    # filter pulls are open
                    # then filter pulls are merged and exclude updated_at greater then closed at
                    pulls = Pull.objects.filter(repository=repo,
                                                author=member.github_account,
                                                pull_merged=False,
                                                updated_at__range=(start_date, end_date)
                                                ).exclude(created_at=F('updated_at')) | Pull.objects.filter(
                                                    repository=repo,
                                                    author=member.github_account,
                                                    pull_merged=True,
                                                    updated_at__range=(start_date, end_date)
                                                    ).exclude(created_at=F('updated_at')
                                                             ).exclude(updated_at__gt=F('closed_at'))

                    for pull in pulls:
                        merged_by = _get_merged_by_kerbroes_id(pull.merged_by)
                        details.append({'patch_number': pull.pull_number,
                                        'repo': repo.repo,
                                        'patch_title': pull.title,
                                        'bug_id': pull.bug_id,
                                        'author': member.kerbroes_id,
                                        'pull_merged': pull.pull_merged,
                                        'commits': pull.commits,
                                        'additions': pull.additions,
                                        'deletions': pull.deletions,
                                        'changed_files': pull.changed_files,
                                        'created_at': pull.created_at,
                                        'updated_at': pull.updated_at,
                                        'closed_at': pull.closed_at,
                                        'merged_by': merged_by,
                                        'patch_url': _build_github_pull_url(repo.owner, repo.repo, pull.pull_number),
                                        })
            response = _paginate_response(details, request)
            return response

        raise APIError(APIError.INVALID_REQUEST_DATA, detail=serializer.errors)
    raise APIError(APIError.INVALID_REQUEST_METHOD, detail='Does Not Support Post Method')


@api_view(['GET'])
def commit_stats(request):
    logger.info('[commit_stats] Received data : %s' % request.query_params)
    if request.method == 'GET':
        serializer = BasesStatsSerializer(data=request.query_params)
        if serializer.is_valid():
            logger.info('[commit_stats] Received data is valid.')
            start_date = serializer.validated_data['start_date']
            end_date = serializer.validated_data['end_date']

            details = []
            repo = Repository.objects.get(id=serializer.validated_data['repository_id'])
            kerbroes_id_list = _stats_type_sortor(serializer.validated_data['stats_type'],
                                                  serializer.validated_data.get('team_code', ''),
                                                  serializer.validated_data.get('kerbroes_id', ''))

            for kerbroes_id in kerbroes_id_list:
                member = Member.objects.get(kerbroes_id=kerbroes_id)
                commits = Commit.objects.filter(repository=repo, email=member.rh_email,
                                                date__range=(start_date, end_date))
                for commit in commits:
                    details.append({'sha': commit.sha[:8],
                                    'author': member.kerbroes_id,
                                    'message': commit.message,
                                    'date': commit.date,
                                    'patch_number': commit.pull.pull_number
                                    })

            response = _paginate_response(details, request)
            return response

        raise APIError(APIError.INVALID_REQUEST_DATA, detail=serializer.errors)
    raise APIError(APIError.INVALID_REQUEST_METHOD, detail='Does Not Support Post Method')


@api_view(['GET'])
def pending_patchs(request):
    logger.info('[pending_patchs] Received data : %s' % request.query_params)
    if request.method == 'GET':
        serializer = PendingSerializer(data=request.query_params)
        if serializer.is_valid():
            logger.info('[pending_patchs] Received data is valid.')
            details = []
            repo = Repository.objects.get(id=serializer.validated_data['repository_id'])
            pulls = Pull.objects.filter(repository=repo, pull_state=1).order_by('created_at')
            today = datetime.today()
            for pull in pulls:
                # filter out upstream author
                if Member.objects.is_serving(pull.author):
                    member = Member.objects.get(github_account=pull.author)
                    total_pending = today - pull.created_at
                    last_updated = today - pull.updated_at
                    review_comments = Comment.objects.filter(comment_type=1, pull_id=pull.id)
                    if pull.draft_state:
                        continue
                    details.append({'patch_number': pull.pull_number,
                                    'repo': repo.repo,
                                    'patch_title': pull.title,
                                    'bug_id': pull.bug_id,
                                    'author': member.kerbroes_id,
                                    'team':member.team.team_name,
                                    'reviews': len(review_comments),
                                    'total_pending': total_pending.days,
                                    'last_updated': last_updated.days,
                                    'create_at': pull.created_at,
                                    'updated_at': pull.updated_at,
                                    'patch_url': _build_github_pull_url(repo.owner, repo.repo, pull.pull_number),
                                    })

            response = _paginate_response(details, request)
            return response
        raise APIError(APIError.INVALID_REQUEST_DATA, detail=serializer.errors)
    raise APIError(APIError.INVALID_REQUEST_METHOD, detail='Does Not Support Post Method')


@api_view(['GET'])
def comment_stats(request):
    logger.info('[comment_stats] Received data : %s' % request.query_params)
    if request.method == 'GET':
        serializer = CommentStatsSerializer(data=request.query_params)
        if serializer.is_valid():
            logger.info('[comment_stats] Received data is valid.')
            start_date = serializer.validated_data['start_date']
            end_date = serializer.validated_data['end_date']

            details = []
            # repo = Repository.objects.get(id=serializer.validated_data['repository_id'])
            repo_id_list = serializer.validated_data['repository_id'].strip().split(',')
            for repo_id in repo_id_list:
                repo = Repository.objects.get(id=repo_id)
                kerbroes_id_list = serializer.validated_data.get('kerbroes_id', '').strip().split(',')
                for kerbroes_id in kerbroes_id_list:
                    member = Member.objects.get(kerbroes_id=kerbroes_id)
                    comments = Comment.objects.filter(author=member.github_account,
                                                      created_at__range=(start_date, end_date),
                                                      pull__repository=repo)
                    for comment in comments:
                        if comment.author != comment.pull.author:
                            details.append({'comment_id': comment.comment_id,
                                            'patch_number': comment.pull.pull_number,
                                            'repo': repo.repo,
                                            'author': member.kerbroes_id,
                                            'body': comment.body,
                                            'created_at': comment.created_at,
                                            'updated_at': comment.updated_at,
                                            'patch_url': _build_github_pull_url(repo.owner, repo.repo, comment.pull.pull_number),
                                            })
            # group comments of same pull together
            values = set(map(lambda x:x['patch_url'], details))
            details_group = [[y for y in details if y['patch_url'] == x] for x in values]
            new_details = [{ 'patch_count':len(details_group),
                             'review_count':len(details),
                             'data':details_group}] 

            response = _paginate_response(new_details, request)
            return response
        raise APIError(APIError.INVALID_REQUEST_DATA, detail=serializer.errors)
    raise APIError(APIError.INVALID_REQUEST_METHOD, detail='Does Not Support Post Method')


@api_view(['GET'])
def bug_status_team(request):
    logger.info('[bug_status] Received data : %s' % request.query_params)
    if request.method == 'GET':
        serializer = BugStatsSerializer(data=request.query_params)
        if serializer.is_valid():
            details = []
            logger.info('[bug_status] Received data is valid.')
            start_date = serializer.validated_data['start_date']
            end_date = serializer.validated_data['end_date']
            kerbroes_id_list = _stats_type_sortor(
                serializer.validated_data['stats_type'],
                serializer.validated_data.get('team_code', ''),
                serializer.validated_data.get('kerbroes_id', ''))
            det = _bug_status(start_date, end_date, kerbroes_id_list)
            if len(kerbroes_id_list) > 1:
                name = 'ALL'
            else:
                name = ''
            det.update({'team': name})
            details.append(det)
            response = _paginate_response(details, request)
            return response
        raise APIError(APIError.INVALID_REQUEST_DATA, detail=serializer.errors)
    raise APIError(APIError.INVALID_REQUEST_METHOD,
                   detail='Does Not Support Post Method')

# from django.shortcuts import render
# from chartit import DataPool, Chart

# def weather_chart_view(request):
#     #Step 1: Create a DataPool with the data we want to retrieve.
#     weatherdata = \
#         DataPool(
#            series=
#             [{'options': {
#                'source': Pull.objects.all()},
#               'terms': [
#                 'author',
#                 'additions',
#                 'deletions']}
#              ])

#     #Step 2: Create the Chart object
#     cht = Chart(
#             datasource = weatherdata,
#             series_options =
#               [{'options':{
#                   'type': 'column',
#                   'stacking': False},
#                 'terms':{
#                   'author': [
#                     'additions',
#                     'deletions']
#                   }}],
#             chart_options =
#               {'title': {
#                    'text': 'Weather Data of Boston and Houston'},
#                'xAxis': {'title': {'text': 'author'}},
#                'chart': { 'zoomType': 'xy'}
#                        })


#     #Step 3: Send the chart object to the template.
#     return render(request, 'test.html',{'weatherchart': cht})
