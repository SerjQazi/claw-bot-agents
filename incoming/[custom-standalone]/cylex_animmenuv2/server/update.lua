local function PrintLog(type, message)
    local color = "^1"
    if type == "success" then color = "^2" end
    print(string.format("^3[cylex_animmenuv2]%s %s^7", color, message))
end

local function CheckUpdate()
    PerformHttpRequest("https://raw.githubusercontent.com/CylexVII/cylex_animmenuv2/master/version.txt", function(err, text, headers)
        local currentVersion = GetResourceMetadata(GetCurrentResourceName(), "version")
        
        if not text then
            PrintLog("error", "Github servers are not accessible right now.")
            return
        end
        
        PrintLog("success", string.format("Latest Version: %s", text))
        PrintLog("success", string.format("Current Version: %s", currentVersion))
        
        local cleanVersion = text:gsub("%s+", "")
        local cleanCurrent = currentVersion:gsub("%s+", "")
        
        if cleanVersion == cleanCurrent then
            PrintLog("success", "Congratulations, you are using the latest version animation menu!")
        else
            PrintLog("error", string.format("You are using an old version of the animation menu. New version: %s", text))
            PrintLog("error", "Please update via keymaster.fivem.net! You can find the update notes on discord.gg/cylexstore")
        end
    end)
end

CheckUpdate()
