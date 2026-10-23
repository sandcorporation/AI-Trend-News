-- Local only (docker-compose.yml, docker-compose.prod-local.yml): let the app user create the
-- pytest database (test_news). Production accounts are made by the coordinator on HeatWave.
GRANT ALL PRIVILEGES ON `test\_news%`.* TO 'news'@'%';
