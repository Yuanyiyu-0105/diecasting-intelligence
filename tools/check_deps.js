// 临时诊断脚本：检查 wewe-rss/apps/server 的依赖是否真的链接好了
const path = require('path');
const fs = require('fs');

const SERVER_DIR = 'D:\\github\\wewe-rss\\apps\\server';
const PKGS = ['@nestjs/core', '@nestjs/common', '@prisma/client', 'prisma',
              'dayjs', 'feed', 'got', 'hbs', 'zod', 'express', 'rxjs'];

function tryResolve(pkg) {
    try {
        // 从 server 目录作为起点解析
        const resolved = require.resolve(pkg + '/package.json', {
            paths: [SERVER_DIR, path.join(SERVER_DIR, 'node_modules')],
        });
        return 'OK   ' + pkg + ' -> ' + resolved;
    } catch (e) {
        return 'FAIL ' + pkg + ' -> ' + e.code;
    }
}

console.log('SERVER_DIR =', SERVER_DIR);
console.log('');

let ok = 0;
for (const p of PKGS) {
    const line = tryResolve(p);
    if (line.startsWith('OK')) ok++;
    console.log(line);
}

console.log('');
console.log(`结果: ${ok}/${PKGS.length} 个依赖可解析`);

// 额外看看 server/node_modules 里到底有什么
const nm = path.join(SERVER_DIR, 'node_modules');
if (fs.existsSync(nm)) {
    const entries = fs.readdirSync(nm);
    console.log('');
    console.log(`apps/server/node_modules 共 ${entries.length} 项:`);
    console.log(entries.join(' | '));
} else {
    console.log('apps/server/node_modules 不存在');
}
