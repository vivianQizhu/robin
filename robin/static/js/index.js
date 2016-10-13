$.fn.datepicker.defaults.format = "yyyy-mm-dd";
$.fn.datepicker.defaults.autoclose = true;
$.fn.datepicker.defaults.todayBtn = true;


function formatTime(timestamp, formater) {
    var date = new Date(timestamp);
    var year = date.getFullYear();
    var month = date.getMonth() + 1;
    var day = date.getDate();
    var hour = date.getHours();
    var min = date.getMinutes();
    var sec = date.getSeconds();
    if (month < 10) {
        month = "0" + month;
    }
    if (day < 10) {
        day = "0" + day;
    }
    if (hour < 10) {
        hour = "0" + hour;
    }
    if (min < 10) {
        min = "0" + min;
    }
    if (sec < 10) {
        sec = "0" + sec;
    }
    return formater.replace("Y", year).replace("M", month).replace("D", day).replace("h", hour).replace("m", min).replace("s", sec);
}


function tips(msg, type, state) {
    var self = this;
    this.msg = msg;
    this.errorMsg = msg;
    this.alertType = type;
    setTimeout(function() {
        self.msg = '';
        self.errorMsg = '';
    }, 3000)
}


function get(url, data) {
    return $.ajax({
        type: "get",
        url: url,
        dataType: "json",
        data: data
    }).done(function(res) {
        return res;
    });
}


var reopData = new Vue({
    el: 'body',
    data: {
        msg: '',
        alertType: 'success',
        category: '',
        hasRes: false,
        repoData: {
            count: 0,
            baseUrl: null,
            nextUrl: null,
            prevUrl: null,
            repos: []
        },
        teamData: {
            count: 0,
            baseUrl: null,
            nextUrl: null,
            prevUrl: null,
            teams: [],
        },
        pendingData: {
            count: 0,
            baseUrl: null,
            nextUrl: null,
            prevUrl: null,
            pendingPatchs: [],
        },
        resData: {},
        repoTmp: [],
        teamTmp: [],
        // Repochecked: false,
        // repository_id: 0,
        pendingPatchs: [],
        type: 1,
        beginTime: '',
        endTime: ''
    },
    watch: {
        repos: function(val) {
            var self = this;
            val.forEach(function(el, index) {
                if (el.checked == undefined) {
                    var status = _.findIndex(self.repoTmp, function(o) {
                        return o.repository_id == el.repository_id;
                    })
                    val.$set(index, {
                        repository_id: el.repository_id,
                        checked: status == -1 ? false: true
                    })
                }
            })
        },
        teams: function(val) {
            var self = this;
            val.forEach(function(el, index) {
                if (el.checked == undefined) {
                    var status = _.findIndex(self.teamTmp, function(o) {
                        return o.team_code == el.team_code;
                    })
                    val.$set(index, {
                        team_code: el.team_code,
                        checked: status == -1 ? false: true
                    })
                }
            })
        }
    },
    methods: {
         submit: function() {
            var self = this;
            if (self.beginTime.length == 0 || self.endTime.length == 0) {
                tips.call(self, 'Start and End dates are required.', 'danger')
                return;
            }

            if (+new Date(self.beginTime) > +new Date(self.endTime)) {
                tips.call(self, 'Start date can not be later than the End date.', 'danger')
                return;
            }
            console.log(this.category);
            if (this.category == 'closedPatchs'){
                get('/api/stats/closed-patchs', {
                    repository_id: self.repoTmp[0].id,
                    stats_type: self.type,
                    kerbroes_id: self.teamTmp[0].members.join(','),
                    start_date: self.beginTime,
                    end_date: self.endTime
                }).then(function(res) {
                    console.log(res)
                    console.log(self.category)
                    self.resData = res,
                    self.hasRes = true,
                    $('#myModal').modal('hide');
                    tips.call(self, 'Query Success', 'success');
                });
            }

        },
        returnBack: function() {
            this.resData = {};
            this.hasRes = false;
        },
        chooseRepo: function(repo) {
            if (!repo.checked) {
                this.repoTmp.push(repo);
                _.sortedUniq(this.repoTmp);
            } else {
                _.remove(this.repoTmp, function(el) {
                    return el.repository_id == repo.repository_id;
                })
            }
        },
        chooseTeam: function(team) {
            if (!team.checked) {
                this.teamTmp.push(team);
                _.sortedUniq(this.teamTmp);

            } else {
                _.remove(this.teamTmp, function(el) {
                    return el.team_code == team.team_code;
                })
            }
        },
        teamStats: function() {
            var self = this;
            // console.log(self.repoTmp[0].repo);
            // console.log(self.teamTmp);
            self.type = 1;
            if (self.repoTmp.length == 0 || self.teamTmp.length == 0){
                tips.call(self, 'Please choose a repository and a team or member.', 'danger')
                return;
            }
            $('#myModal').modal('show')
        },
        showPending: function(repo){
            var self = this;
            var repository_id = {'repository_id': repo.id};
            get('/api/stats/pending-patchs/', repository_id).then(function(res) {
            self.pendingData.pendingPatchs = res.results;
            self.pendingData.count = res.count;
            self.pendingData.nextUrl = res.next;
            self.pendingData.prevUrl = res.previous;
            // console.log(self.pendingData.pendingPatchs)
            $("#pending-patchs").toggle()
            // bugs here
            if ($("#p-btn" + repo.id.toString()).text() ==  "Show")
                {
                    $("#p-btn" + repo.id.toString()).text("Close");
                }
                else 
                {
                     $("#p-btn" + repo.id.toString()).text("Show");
                }
            });
        }
    },
    created: function() {
        var self = this;
        // this.apiUrl = '/api/repositories/';
        get('/api/repositories/').then(function(res) {
            var time = formatTime(+new Date(), 'Y-M-D');
            self.repoData.repos = res.results;
            self.repoData.count = res.count;
            self.repoData.nextUrl = res.next;
            self.repoData.prevUrl = res.previous;
            $('#start_date').datepicker({
                defaultViewDate: time
            });
            $('#end_date').datepicker();
            self.beginTime = time;
            self.endTime = time;
        });

        get('/api/teams/').then(function(res) {
            var time = formatTime(+new Date(), 'Y-M-D');
            self.teamData.teams = res.results;
            self.teamData.count = res.count;
            self.teamData.nextUrl = res.next;
            self.teamData.prevUrl = res.previous;
            // console.log(self.teamData.teams);
        });
    }
});

