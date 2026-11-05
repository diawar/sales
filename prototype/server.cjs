const http=require('node:http');
const fs=require('node:fs');
const path=require('node:path');
const root=path.join(__dirname,'dist');
const types={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.svg':'image/svg+xml'};
const server=http.createServer((req,res)=>{const pathname=new URL(req.url,'http://localhost').pathname;const allowed={'/':'index.html','/index.html':'index.html','/style.css':'style.css','/goszakup.css':'goszakup.css','/match.css':'match.css','/icons.svg':'icons.svg','/app.js':'app.js','/details.js':'details.js','/details.css':'details.css'};const file=allowed[pathname];if(!file){res.writeHead(404);return res.end('Not found');}fs.readFile(path.join(root,file),(err,data)=>{if(err){res.writeHead(500);return res.end('Read error');}res.writeHead(200,{'Content-Type':types[path.extname(file)],'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(data);});});
server.listen(8173,'127.0.0.1',()=>console.log('Local: http://127.0.0.1:8173'));


