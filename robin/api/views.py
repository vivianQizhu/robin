import logging
import urllib
import xlsxwriter
from datetime import datetime, date, timedelta

from io import BytesIO
from commons.exceptions import APIError
from django.db.models import F
from django.http import HttpResponse
from members.models import Team, Member
from rest_framework.decorators import api_view
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView
from statistics.models import (Repository,
                               Pull,
                               Commit,
                               Comment,
                               ProductBug,
                               MultiArchProductBug)

from .serializers import (RepositorySerializer,
                          TeamSerializer,
                          PendingSerializer,
                          BasesStatsSerializer,
                          CommentStatsSerializer,
                          MemberSerializer,
                          BugStatsSerializer,
                          BugSummarySerializer)

from members.models import MULTI_ARCH_TYPE as MULTI_ARCH_TYPE

logger = logging.getLogger(__name__)

PRODUCT_BUG_DATA = []

high_above_f = '&f5=OP&f6=priority&f7=bug_severity&j5=OR'
high_above_o = '&o6=anywordssubstr&o7=anywordssubstr'
high_above_v = '&v6=urgent%2Chigh&v7=urgent%2Chigh'
robin_list_id = 'ROBIN_LIST_ID'
robin_role = 'ROBIN_ROLE'


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


def _get_bz_url(start_date, end_date, kerbroes_id_list,
                extra_field=None, exclude_acceptance=False, desired_qa_whiteboard=''):

    valid_bz_url = ('&classification=Red%%20Hat&list_id=%s&query_format=advanced'
                    '&f1=keywords&f2=%s&f3=cf_zstream_target_release' %
                    (robin_list_id, robin_role))
    if exclude_acceptance:
        valid_bz_url += '&f4=cf_qa_whiteboard'
    if desired_qa_whiteboard:
        valid_bz_url += '&f5=cf_qa_whiteboard'
    valid_bz_url += high_above_f
    valid_bz_url += '&o1=nowordssubstr&o2=anywordssubstr&o3=isempty'

    valid_bz_url += high_above_o
    if exclude_acceptance:
        valid_bz_url += '&o4=notsubstring'
    if desired_qa_whiteboard:
        valid_bz_url += '&o5=anywordssubstr'
    valid_bz_url += ('&chfield=%%5BBug%%20creation%%5D&chfieldfrom=%s&chfieldto=%s'
                     % (str(start_date)[:10], str(end_date)[:10]))

    fields = {
        'component': ['qemu-kvm', 'kernel', 'virtio-win', 'seabios', 'edk2',
                      'slof', 'qemu-guest-agent', 'dtc', 'kernel-rt', 'ovmf',
                      'libtpms', 'virglrenderer', 'qemu-kvm-rhev', 'kernel-rt',
                      'qemu-kvm-ma', 'kernel-alt', 'mdevctl']}
    if extra_field:
        fields.update(extra_field)

    filters = {'v1': ["ABIAssurance", "TechPreview", "ReleaseNotes", "Tracking",
                     "Task", "HardwareEnablement", "SecurityTracking",
                     "TestOnly", "Improvement", "FutureFeature", "Rebase",
                     "FeatureBackport", "Documentation", "OtherQA", "RFE"],
               'v2': kerbroes_id_list}
    if exclude_acceptance:
        filters.update({'v4': ['acceptance']})
    if desired_qa_whiteboard:
        filters.update({'v5': desired_qa_whiteboard.split(',')})

    for key, value in fields.items():
        for op in value:
            valid_bz_url += '&%s=' % key
            valid_bz_url += urllib.quote(op)

    for key, value in filters.items():
        valid_bz_url += '&%s=' % key
        for op in value[:-1]:
            valid_bz_url += urllib.quote('%s,' % op)
        valid_bz_url += urllib.quote(value[-1])

    valid_bz_url += high_above_v
    valid_bz_url += '&api_key=mLPREvS9ArB97djTLlZBmRKeqkp8jDYrCeLX4U58'
    return valid_bz_url


def _bug_status(start_date, end_date, kerbroes_id_list,
                exclude_acceptance=False, arch=None):
    details = {}
    cgi_base_url = (
        'https://bugzilla.redhat.com/buglist.cgi?columnlist=product'
        '%2Ccomponent%2Cassigned_to%2Cbug_status%2Cresolution'
        '%2Cshort_desc%2Cflagtypes.name%2Cqa_contact%2Creporter'
        '%2Ckeywords%2Cpriority%2Cbug_severity%2Ccf_qa_whiteboard'
        '%2Cversion%2Cplatform')
    end_date = end_date + timedelta(days=1)

    product = {'rhel8': ["Red Hat Enterprise Linux 8",
                         "Red Hat Enterprise Linux Advanced Virtualization"],
               'rhel9': ["Red Hat Enterprise Linux 9"]}
    product_names = product.keys()
    product_names.append('all')

    desired_qa_whiteboard = ''
    if not arch:
        platform_field = {
            'rep_platform': ["Unspecified", "All", "x86_64", "ppc64", "ppc64le"]}
        bug_model = ProductBug
    else:
        desired_feature = {
            's390 s390x': 'cpu,memory,virtual-block,storage-vm-migration',
            'aarch64': 'virtual network,general operation,sve,cpu,'
                       'memory,virtual-block,migration'}
        if arch in desired_feature.keys():
            desired_qa_whiteboard = desired_feature[arch]
        platform_field = {'rep_platform': arch.split()}
        bug_model = MultiArchProductBug

    with open('/tmp/robin.log', 'a') as f:
        f.write(str(bug_model))
    bz_url_all = _get_bz_url(start_date, end_date, kerbroes_id_list,
                             extra_field=platform_field,
                             exclude_acceptance=exclude_acceptance,
                             desired_qa_whiteboard=desired_qa_whiteboard)

    def get_num_valid(list_id, bz_filter='reporter', high=False):
        fields = {
            'bug_status': ['NEW', 'ASSIGNED', 'POST', 'MODIFIED', 'ON_QA',
                           'VERIFIED', 'CLOSED'],
            'resolution': ["---", "CURRENTRELEASE", "ERRATA"]}
        fields.update(platform_field)
        bz_url = _get_bz_url(start_date, end_date, kerbroes_id_list,
                             extra_field=fields,
                             exclude_acceptance=exclude_acceptance,
                             desired_qa_whiteboard=desired_qa_whiteboard)
        extra_filter = {'resolution': 'VALID'}
        return get_num_and_link(list_id, bz_filter,
                                bz_url, high, extra_filter=extra_filter)

    def get_num_fixed(list_id, bz_filter='reporter', high=False):
        fields = {
            'bug_status': ['CLOSED', 'MODIFIED', 'VERIFIED'],
            'resolution': ["---", "CURRENTRELEASE", "ERRATA"]}
        fields.update(platform_field)
        bz_url = _get_bz_url(start_date, end_date, kerbroes_id_list,
                             extra_field=fields,
                             exclude_acceptance=exclude_acceptance,
                             desired_qa_whiteboard=desired_qa_whiteboard)
        extra_filter = {'status': 'FIXED'}
        return get_num_and_link(list_id, bz_filter,
                                bz_url, high, extra_filter=extra_filter)

    def get_num_invalid(list_id, bz_filter='reporter', high=False):
        fields = {
            'bug_status': ['CLOSED'],
            'resolution': ["NOTABUG", "DUPLICATE", "INSUFFICIENT_DATA",
                           "CANTFIX", "NEXTRELEASE", "WORKSFORME","WONTFIX"]}
        fields.update(platform_field)
        bz_url = _get_bz_url(start_date, end_date, kerbroes_id_list,
                             extra_field=fields,
                             exclude_acceptance=exclude_acceptance,
                             desired_qa_whiteboard=desired_qa_whiteboard)
        extra_filter = {'resolution': 'INVALID'}
        return get_num_and_link(list_id, bz_filter,
                                bz_url, high, extra_filter=extra_filter)

    def get_num_and_link(list_id, bz_filter='reporter', url=bz_url_all,
                         high=False, extra_filter=None):
        product_num = dict.fromkeys(product_names, 0)
        url_list = {}
        url_r = cgi_base_url + url.replace(
            robin_list_id, list_id).replace(robin_role, bz_filter)
        if not high:
            url_r = url_r.replace(high_above_f, '').replace(
                high_above_o, '').replace(high_above_v, '')
        product_filter_str = ''
        for key, value in product.items():
            url_r_tmp = url_r
            for p_name in value:
                product_filter_tmp = '&product=' + urllib.quote(p_name)
                url_r_tmp += product_filter_tmp
                product_filter_str += product_filter_tmp
                if p_name == 'Red Hat Enterprise Linux Advanced Virtualization':
                    continue
                filter_dict = {'bug_product': p_name}
                if arch and arch in [i[1] for i in MULTI_ARCH_TYPE[1:]]:
                    filter_dict.update({'hardware': arch})
                if extra_filter:
                    filter_dict.update(extra_filter)
                for kerbroes_id in kerbroes_id_list:
                    filter_dict.update({bz_filter: kerbroes_id})
                    if high:
                        filter_dict.update({'priority': 'high'})
                    with open('/tmp/robin.log', 'a') as f:
                        f.write('filter: %s' % str(filter_dict))
                    bugs = bug_model.objects.filter(**filter_dict).filter(
                        created_at__range=(start_date, end_date))
                    if exclude_acceptance or desired_qa_whiteboard:
                        bugs = bugs.exclude(qa_whiteboard__contains='not_desired')
                    if high:
                        filter_dict.update({'priority': 'urgent'})
                        u_bugs = bug_model.objects.filter(**filter_dict).filter(
                            created_at__range=(start_date, end_date))
                        if exclude_acceptance or desired_qa_whiteboard:
                            u_bugs = u_bugs.exclude(qa_whiteboard__contains='not_desired')
                        bugs = bugs | u_bugs
                    if bz_filter == 'reporter':
                        new_count = 0
                        for bug in bugs:
                            if (bug.qa_contact in kerbroes_id_list) or (bug.qa_contact == 'virt-bugs'):
                                new_count += 1
                    else:
                        new_count = bugs.count()
                    product_num.update({key: new_count + product_num[key]})
                    product_num.update({'all': product_num['all'] + new_count})
            url_list.update({key: url_r_tmp})
        url_list.update({'all': url_r + product_filter_str})
        with open('/tmp/robin.log', 'a') as f:
            f.write("product_num, url_list: %s\n %s" % (str(product_num), str(url_list)))

        return product_num, url_list

    reported_valid, reported_urls_valid = get_num_valid('11627322', 'reporter')
    qa_contact, qa_contact_urls = get_num_valid(
        '11627320', 'qa_contact')
    reported_high, reported_urls_high = get_num_valid(
        '11627322', 'reporter', True)
    qa_contact_high, qa_contact_urls_high = get_num_valid(
        '11627320', 'qa_contact', True)

    reported_invalid, reported_url_invalid = get_num_invalid('11627322', 'reporter')
    reported_fixed, reported_url_fixed = get_num_fixed('11627322', 'reporter')

    def ratio(num, den):
        valid_bz_ratio = 0
        if den != 0:
            valid_bz_ratio = "%.2f%%" % (float(num)/float(den)*100)
        return valid_bz_ratio

    for product_name in product_names:
        valid_reported_num = reported_valid[product_name]
        valid_qa_contact_num = qa_contact[product_name]
        total_catch_bz_ratio = ratio(valid_reported_num, valid_qa_contact_num)
        valid_reported_num_high = reported_high[product_name]
        valid_qa_contact_num_high = qa_contact_high[product_name]
        high_catch_ratio = ratio(valid_reported_num_high, valid_qa_contact_num_high)
        invalid_num = reported_invalid[product_name]
        fixed_num = reported_fixed[product_name]
        invalid_ratio = ratio(invalid_num, invalid_num + valid_reported_num)
        fixed_ratio = ratio(fixed_num, valid_reported_num)
        details.update(
            {'total_catch_bz_ratio_%s' % (product_name): total_catch_bz_ratio,
             'valid_reported_%s' % (product_name): valid_reported_num,
             'valid_reported_url_%s' % (product_name): reported_urls_valid[product_name],
             'qa_contact_%s' % (product_name): valid_qa_contact_num,
             'qa_contact_url_%s' % (product_name): qa_contact_urls[product_name],
             'high_catch_ratio_%s' % (product_name): high_catch_ratio,
             'high_reported_%s' % (product_name): valid_reported_num_high,
             'high_reported_url_%s' % (product_name): reported_urls_high[product_name],
             'high_qa_contact_%s' % (product_name): valid_qa_contact_num_high,
             'high_qa_contact_url_%s' % (product_name): qa_contact_urls_high[product_name],
             'fixed_num_%s' % (product_name): fixed_num,
             'fixed_url_%s' % (product_name): reported_url_fixed[product_name],
             'fixed_ratio_%s' % (product_name): fixed_ratio,
             'invalid_num_%s' % (product_name): invalid_num,
             'invalid_url_%s' % (product_name): reported_url_invalid[product_name],
             'invalid_ratio_%s' % (product_name): invalid_ratio
             })

    with open('/tmp/robin.log', 'a') as f:
        f.write(str(details))
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
    """ returns bug status based on the product type(x86/ppc or arm/s390)"""

    details = []

    def _get_x86_n_ppc_bug_data(self, start_date, end_date):
        """
        Generate bug data for the whole group
        """
        members = Member.objects.all()
        kerbroes_id_list = [member.kerbroes_id for member in members]
        kerbroes_id_list.append('virt-bugs')
        d = _bug_status(start_date, end_date, kerbroes_id_list)
        d.update({'team': 'KVM_QE_ALL'})
        self.details.append(d)
        # Subteam data
        teams = Team.objects.all()
        for team in teams:
            members = Member.objects.filter(team=team)
            kerbroes_id_list = [member.kerbroes_id for member in members]
            # Exclude acceptance for sub teams besides multi-arch team(qzhang)
            excl_acpt = False if team.team_code == 'qzhang' else True
            d = _bug_status(start_date, end_date, kerbroes_id_list, excl_acpt)
            d.update({'team': team.team_name})
            self.details.append(d)

    def _get_multi_arch_bug_data(self, start_date, end_date):
        """
        Generate bug data for multi arch group
        """
        all_members = []
        for arch in MULTI_ARCH_TYPE[1:]:
            with open('/tmp/robin.log', 'a') as f:
                f.write('arch: %s' % str(arch))
            members = Member.objects.filter(multi_arch_type=arch[0])
            kerbroes_id_list = [member.kerbroes_id for member in members]
            all_members.extend(kerbroes_id_list)
            with open('/tmp/robin.log', 'a') as f:
                f.write('kerbroes_id_list: %s' % str(kerbroes_id_list))
            d = _bug_status(start_date, end_date, kerbroes_id_list, arch=arch[1])
            d.update({'team': arch[1].replace(' ', '/')})
            self.details.append(d)
        all_archs = ' '.join([i[1] for i in MULTI_ARCH_TYPE[1:]])
        d = _bug_status(start_date, end_date, all_members, arch=all_archs)
        d.update({'team': 'ALL'})
        self.details.append(d)

    def get(self, request, format=None):
        logger.info('[bug_status] Received data is valid.')
        serializer = BugSummarySerializer(data=request.query_params)
        self.details = []
        if serializer.is_valid():
            product_type = serializer.validated_data['product_type']
            end_date = datetime.today()
            year = date.today().year
            start_date = datetime(year, 1, 1, 0, 0, 0)
            with open('/tmp/robin.log', 'a') as f:
                f.write(str(product_type))
            if product_type == 1:
                self._get_x86_n_ppc_bug_data(start_date, end_date)
            elif product_type == 2:
                self._get_multi_arch_bug_data(start_date, end_date)
            global PRODUCT_BUG_DATA
            PRODUCT_BUG_DATA = self.details
            response = _paginate_response(self.details, request)
            return response
        raise APIError(APIError.INVALID_REQUEST_DATA, detail=serializer.errors)


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
            new_details = [{'patch_count': len(details_group),
                            'review_count': len(details),
                            'data': details_group}]

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
            team_code = serializer.validated_data.get('team_code', '')
            product_type = serializer.validated_data.get('product_type', '')
            if product_type == 2:
                all_members = []
                for arch in MULTI_ARCH_TYPE[1:]:
                    members = Member.objects.filter(multi_arch_type=arch[0])
                    kerbroes_id_list = [member.kerbroes_id for member in members]
                    all_members.extend(kerbroes_id_list)
                    d = _bug_status(start_date, end_date, kerbroes_id_list,
                                    arch=arch[1])
                    d.update({'team': arch[1].upper()})
                    details.append(d)
                    if len(kerbroes_id_list) > 1:
                        for kerbroes_id in kerbroes_id_list:
                            d = _bug_status(start_date, end_date,
                                            [kerbroes_id], arch=arch[1])
                            d.update({'team': kerbroes_id})
                            details.append(d)
                all_archs = ' '.join([i[1] for i in MULTI_ARCH_TYPE[1:]])
                d = _bug_status(start_date, end_date, all_members,
                                arch=all_archs)
                d.update({'team': 'ALL'})
                details.append(d)
            else:
                kerbroes_id_list = _stats_type_sortor(
                    serializer.validated_data['stats_type'],
                    team_code,
                    serializer.validated_data.get('kerbroes_id', ''))
                excl_accept = False if 'qzhang' in 'team_code' else True
                team = Team.objects.filter(team_code='qzhang')
                q_members = Member.objects.filter(team=team)
                qzhang_members = [member.kerbroes_id for member in q_members]
                if set(qzhang_members) & set(kerbroes_id_list):
                    excl_accept = False
                det = _bug_status(start_date, end_date, kerbroes_id_list, excl_accept)
                if len(kerbroes_id_list) > 1:
                    name = 'ALL'
                else:
                    name = kerbroes_id_list[0]
                det.update({'team': name})
                details.append(det)
                if len(kerbroes_id_list) > 1:
                    for kerbroes_id in kerbroes_id_list:
                        d = _bug_status(start_date, end_date, [kerbroes_id], excl_accept)
                        d.update({'team': kerbroes_id})
                        details.append(d)
            global PRODUCT_BUG_DATA
            PRODUCT_BUG_DATA = details
            response = _paginate_response(details, request)
            return response
        raise APIError(APIError.INVALID_REQUEST_DATA, detail=serializer.errors)
    raise APIError(APIError.INVALID_REQUEST_METHOD,
                   detail='Does Not Support Post Method')


@api_view(['GET'])
def export_excel(request):
    logger.info('[export excel]' % request.query_params)
    if request.method == 'GET':
        output = BytesIO()
        ws = xlsxwriter.Workbook(output)
        w = ws.add_worksheet()
        style = ws.add_format({'bold': True})

        w.write(0, 0, u'Team Name/Member Name', style)
        w.write(0, 1, u'Catch BZs Ratio', style)
        w.write(0, 2, u'Reported', style)
        w.write(0, 3, u'QA Contact', style)
        w.write(0, 4, u'High&Above Catch Ratio', style)
        w.write(0, 5, u'High&Above Reported', style)
        w.write(0, 6, u'High&Above QA Contact', style)
        w.write(0, 7, u'Fixed Ratio', style)
        w.write(0, 8, u'Fixed Bz', style)
        w.write(0, 9, u'Invalid Ratio', style)
        w.write(0, 10, u'Reported Invalid', style)

        global PRODUCT_BUG_DATA
        len_list = len(PRODUCT_BUG_DATA)
        w.write(1, 0, u'Summary', style)
        w.write(2 + len_list, 0, u'RHEL 8', style)
        w.write(3 + len_list * 2, 0, u'RHEL 9', style)

        for i, detail in enumerate(PRODUCT_BUG_DATA):
            col_sum = i + 2
            col_next = col_sum
            for product in ('all', 'rhel8', 'rhel9'):
                valid_reported = str(detail['valid_reported_%s' % product])
                valid_reported_url = str(detail['valid_reported_url_%s' % product])
                qa_contact = str(detail['qa_contact_%s' % product])
                qa_contact_url = str(detail['qa_contact_url_%s' % product])
                reported_h = str(detail['high_reported_%s' % product])
                reported_url_h = str(detail['high_reported_url_%s' % product])
                qa_contact_h = str(detail['high_qa_contact_%s' % product])
                qa_contact_url_h = str(detail['high_qa_contact_url_%s' % product])
                fixed_num = str(detail['fixed_num_%s' % product])
                fixed_url = str(detail['fixed_url_%s' % product])
                invalid_num = str(detail['invalid_num_%s' % product])
                invalid_url = str(detail['invalid_url_%s' % product])

                w.write(col_next, 0, detail['team'])
                w.write(col_next, 1,
                        detail['total_catch_bz_ratio_%s' % product])
                w.write_url(col_next, 2, valid_reported_url, string=valid_reported)
                w.write_url(col_next, 3, qa_contact_url, string=qa_contact)
                w.write(col_next, 4, detail['high_catch_ratio_%s' % product])
                w.write_url(col_next, 5, reported_url_h, string=reported_h)
                w.write_url(col_next, 6, qa_contact_url_h, string=qa_contact_h)
                w.write(col_next, 7, detail['fixed_ratio_%s' % product])
                w.write_url(col_next, 8, fixed_url, string=fixed_num)
                w.write(col_next, 9, detail['invalid_ratio_%s' % product])
                w.write_url(col_next, 10, invalid_url, string=invalid_num)
                col_next = col_next + len_list + 1

        ws.close()
        output.seek(0)
        response = HttpResponse(output.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response[
            'Content-Disposition'] = 'attachment;filename={0}'.format(
            'ProductStatisticReport.xlsx')
        output.close()
        return response
    raise APIError(APIError.INVALID_REQUEST_METHOD,
                   detail='Does Not Support Post Method')
