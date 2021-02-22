# Robin
-----
### What is Robin
Robin is a github patch information retrieval web service.

### Features
> * Query submitted, updated and closed patch information.
> * Tracking patch reviews of members.
> * Listing pending patch information of a chosen repository.
> * Resources (repo, team and member) management.

服务器
ssh root@10.66.8.100
密码: redhat 或者 kvmautotest

Username: root  
Password: kvmautotest1qaz2wsx  
This **root** is a superuser, which has all the permissions.  
  
Username: admin  
Password: kvmautotest  
This **admin** is a staff, which can only add or change a team/member/repo (It can be set by **root**).  
  
The **admin** is sufficient for daily usage. You can distribute these two accounts accordingly.  
Login through: [http://10.66.8.100/_admin](http://10.66.8.100/_admin)

项目目录:
`/home/hachen/projects/robin`
github access token:
`/home/hachen/resources/robin/keys/access_token.txt`
(需要被更换成维护者个人的access token, 不然请求次数有限)

主要有三项任务：
部署或重启服务:
场景： 代码更新, 服务器断电关闭等
1. on your machine:`git clone https://github.com/hereischen/robin.git`
2. `cd robin/deploy_tools`
3. `./deploy_robin_test.sh` and follow the instructions
4. `service nginx restart`

刷过往数据：
场景： 服务器断电关闭某日数据未写入数据库
1. `ssh root@10.66.8.100`
2. `cd /home/hachen/projects/robin/`
3. `source .env/bin/activate`
4. `cd robin`
5. `vim crons/copy.py +21`根据所需要的刷入日期修改
6. `python manage.py shell --settings=robin.settings.test`
7. `import crons.copy as c`
