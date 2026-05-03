// test.mjs
import plugin from './index.js';

// 模擬 Openclaw 的 API 物件
const mockApi = {
    registeredTool: null,
    registerTool(tool) {
        this.registeredTool = tool;
        console.log(`✅ 成功註冊工具: "${tool.name}"\n`);
    }
};

async function runTest() {
    // 1. 執行外掛註冊
    plugin.register(mockApi);

    if (mockApi.registeredTool) {
        console.log("開始測試執行 execute()... 測試地點: 'Taipei'\n");
        // 2. 直接拿註冊進來的工具之 execute 函式來測試
        const result = await mockApi.registeredTool.execute(
            "test-id",
            { location: "Taipei" } // 丟假參數進去
        );

        console.log("--- 執行結果 ---");
        console.log(JSON.stringify(result, null, 2));
    } else {
        console.log("註冊工具失敗。");
    }
}

runTest();
