local isMenuOpen = false
local isJsLoaded = false
local shortcuts = {}

local function debugPrint(...)
    if Config and Config.Debug then
        print(...)
    end
end
-- Overwrite print with debugPrint
print = debugPrint

function openMenu(state)
    local focus = state
    if state == nil then
        focus = not isMenuOpen
    end
    isMenuOpen = focus
    SetNuiFocus(isMenuOpen, isMenuOpen)
    SendNUIMessage({
        action = "open",
        state = isMenuOpen
    })
end

RegisterNUICallback("close", function(data, cb)
    openMenu(false)
    cb("ok")
end)

RegisterNUICallback("jsLoaded", function(data, cb)
    isJsLoaded = true
    cb("ok")
end)

CreateThread(function()
    local function loadJsonFile()
        local content = LoadResourceFile(GetCurrentResourceName(), "animations/AnimationList.json")
        if not content or content == "" then
            return {}
        end
        return json.decode(content)
    end

    while not isJsLoaded do
        print("Emotes: Waiting for JS to load")
        Wait(100)
    end

    Config.AllAnimations = {}
    print("Emotes: Loading")

    for categoryName, categoryData in pairs(Config.Categories) do
        local animations = Config.Animations[categoryData.id]
        if animations then
            for animIndex, animData in pairs(animations) do
                if animData.dict and animData.dict ~= "" then
                    if not DoesAnimDictExist(animData.dict) then
                        print("Emotes: Not found: " .. animData.dict)
                    end
                end

                if not animData.label then
                    if animData.category == "dances" then
                        animData.label = string.format("Dance %s", animIndex)
                    end
                end

                if not animData.id then
                    animData.id = string.format("%s_%s", animData.category, animIndex)
                    if animData.category == "walks" then
                        animData.gif = string.format("%s.webp", animData.id)
                    end
                end

                table.insert(Config.AllAnimations, animData)
            end
        end
    end

    if Config.CustomEmotes then
        local customEmotes = loadJsonFile()
        for _, categoryEmotes in pairs(customEmotes) do
            for _, emoteData in pairs(categoryEmotes) do
                local shouldAdd = true
                if emoteData.dict and emoteData.dict ~= "" then
                    if not DoesAnimDictExist(emoteData.dict) then
                        print("Emotes: Not found: " .. emoteData.dict)
                        shouldAdd = false
                    end
                end

                if shouldAdd and not (emoteData.category == "erp" or emoteData.category == 'adult' or (emoteData.label and string.find(emoteData.label, "18"))) then
                    table.insert(Config.AllAnimations, emoteData)
                end
            end
        end
    end

    SendNUIMessage({
        action = "load",
        animations = Config.AllAnimations,
        categories = Config.Categories,
        locales = Locales[Config.Language]
    })
    print("Emotes: Loaded")
end)

RegisterNUICallback("stopAnim", function(data, cb)
    onEmoteCancel() -- Assumed global or defined elsewhere? It wasn't defined in this file. 
                    -- Looking at directory list, there is 'dpFunctions.lua' and 'functions.lua', likely defined there.
    cb("ok")
end)

RegisterNUICallback("onAnimClicked", function(data, cb)
    print("onAnimClicked", data.animation)
    onAnimTriggered(data.animation) -- Also likely external
    cb("ok")
end)

RegisterNUICallback("getShortcuts", function(data, cb)
    print("getShortcuts", json.encode(data))
    shortcuts = data.shortcuts
    cb("ok")
end)
