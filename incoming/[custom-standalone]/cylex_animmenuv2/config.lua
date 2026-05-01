Config = {
    Debug = false, -- If you want to see the debug prints, set this to true
    -- If you want to change the key, go to https://docs.fivem.net/docs/game-references/controls/
    OpenKey = 'F6', -- Key to open the menu

    -- If you want to change the language, go to
    Language = 'en', -- en, tr
    MultipleAnim = true,

    CustomEmotes = true, -- If you want to use custom emotes (AnimationList.json), set this to true ( YOU NEED TO BUY BUNDLE FOR THIS, IF YOU DON'T HAVE BUNDLE, SET THIS TO FALSE)
    PersistentExpressions = true, -- If you want to keep the expressions active when u relog, set this to true
    PersistentWalkStyle = true, -- If you want to keep the walk styles active when u relog, set this to true

    -- You can find the list of keys on this website
    -- https://docs.fivem.net/docs/game-references/controls/
    AcceptBind = 246, -- Key to accept the emote (Currently: Y) 
    RefuseBind = 244, -- Key to refuse the emote (Currently: M)

    Ragdoll = {
        enabled = true,
        keybind = 'U', -- Get the button string from here https://docs.fivem.net/docs/game-references/input-mapper-parameter-ids/keyboard
        ragdollAsToggle = true,
    },


    Crouch = {
        enabled = true,
        keybindEnabled = false, -- If true, crouching will use keybinds otherwise you need to use comand.
        keybind = 'C', -- The default crouch keybind, get the button string here: https://docs.fivem.net/docs/game-references/input-mapper-parameter-ids/keyboard/
        crouchOverride = false, -- If true, you won't enter stealth mode even if the crouch key and the "duck" key are the same.
    },

    Crawl = {
        enabled = false, -- If true, crawling will be enabled, otherwise you need to use comand.
        keybindEnabled = true, -- If true, crawling will use keybinds.
        keybind = 'RCONTROL', -- The default crawl keybind, get the button string here: https://docs.fivem.net/docs/game-references/input-mapper-parameter-ids/keyboard/
    },
  

    AnimPos = {
        EnableAnimPos = true, -- If you want to disable animation position, set this to false
        MaxDistance = 25.0, -- How far they can move away from the starting distance of the anim pos.
        FreeModeMaxDistance = 8.0, -- How far they can move away from the starting distance of the anim pos in free mode.
        TeleportBackOnCancel = false, -- If you want to teleport back to the starting position when the emote is canceled ON ANIMATION POSITION, set this to true

        up = 241, -- UP (Currently: Scroll wheel up)
        down = 242, -- DOWN (Currently: Scroll wheel down)

        left = 174, -- LEFT (Currently: ARROW LEFT)
        right = 175, -- RIGHT (Currently: ARROW RIGHT)

        forward = 172, -- FORWARD (Currently: ARROW UP)
        backward = 173, -- BACKWARD (Currently: ARROW DOWN)

        rotateLeft = 52, -- Rotate Left (Currently: Q)
        rotateRight = 51, -- Rotate tRight (Currently: E)

        followMouse = 47, -- Freeze Mouse Follow (Currently: G)
        done = 191, -- Done (Currently: ENTER)
        cancel = 73, -- Done (Currently: ENTER)

        KeyInfos = {
           {label = 'Right', key = 175},
           {label = 'Left', key = 174},
           {label = 'Backward', key = 173},
           {label = 'Forward', key = 172},
           {label = 'Rotate Right', key = 51},
           {label = 'Rotate Left', key = 52},
           {label = 'Up / Down', key = 348},
           {label = 'Enable/Disable Mouse Follow', key = 47},
           {label = 'Cancel', key = 73},
           {label = 'Done', key = 191},
        }
    },
    

    Categories = {
        {
            id = 'all',
            label = 'All',
            icon = 'fas fa-house',
        },
        {
            id = 'favorites',
            label = 'Favorites',
            icon = 'fas fa-star',
        },
        {
            id = 'emotes',
            label = 'General',
            icon = 'fas fa-male',
        },
        {
            id = 'dances',
            label = 'Dances',
            icon = 'fas fa-running',
        },
        {
            id = 'expressions',
            label = 'Expressions',
            icon = 'fas fa-laugh-beam',
        },
        {
            id = 'walks',
            label = 'Walks',
            icon = 'fas fa-running',
        },
        {
            id = 'custom',
            label = 'Custom Emotes',
            icon = 'fas fa-male',
        },

    },
}

Notify = function(msg, type)
    SetNotificationTextEntry("STRING")
    AddTextComponentString(msg)
    DrawNotification(true, true)
    
    print('notify', msg)
end

Lang = function(msg)
    return Locales[Config.Language][msg] or 'Translation not found'
end

CreateThread(function()
    RegisterNetEvent('emotes:openMenu', function()
        openMenu()
    end)
    
    RegisterCommand('emotemenu', function()
        openMenu()
    end)
    RegisterKeyMapping('emotemenu', 'Open Emotes Menu', 'keyboard', Config.OpenKey)
    
    RegisterCommand('emotecancel', function()
        onEmoteCancel()
    end)
    RegisterKeyMapping('emotecancel', 'Emote Cancel', 'keyboard', 'DELETE')

    -- Animation commands
    RegisterCommand('walk', function(src, args)
        local emote = args[1]
        if not emote then return end
        local emote = emote:lower()
    
        if emote == 'c' or emote == 'cancel' then
            return onWalkCancel()
        end
    
        for _, animation in pairs(Config.AllAnimations) do
            if animation.id == emote and animation.category == 'walks' then
                return onWalk(animation)
            end
        end
    end)

    TriggerEvent('chat:addSuggestion', '/walk', 'Play a walk.', {
        { name = "walk", help = "Walk name" }
    })

    RegisterCommand('expression', function(src, args)
        local emote = args[1]
        if not emote then return end
        local emote = emote:lower()
    
        if emote == 'c' or emote == 'cancel' then
            return onExpressionCancel()
        end
    
        for _, animation in pairs(Config.AllAnimations) do
            if animation.id == emote and animation.category == 'expressions' then
                return onExpression(animation)
            end
        end
    end)

    TriggerEvent('chat:addSuggestion', '/expression', 'Play an expression.', {
        { name = "expression", help = "Expression name" }
    })
    
    RegisterCommand('e', function(source, args)
        local emote = args[1]
        if not emote then return end
        local emote = emote:lower()
    
        if emote == 'c' or emote == 'cancel' then
            return onEmoteCancel()
        end
    
        for _, animation in pairs(Config.AllAnimations) do
            if animation.id == emote and animation.category ~= 'walks' and animation.category ~= 'expressions' then
               return onAnimTriggered(animation)
            end
        end
    
        print('Animation not found: ' .. emote)
    end)

    TriggerEvent('chat:addSuggestion', '/e', 'Play an emote.', {
        { name = "emote", help = "Emote name" }
    })
end)

RegisterCommand('idlecam', function()
    local ped = PlayerPedId()
    local idleCamDisabled = GetResourceKvpString("idleCam") == "off"
    if idleCamDisabled then
        DisableIdleCamera(false)
        SetPedCanPlayAmbientAnims(ped, true)
        SetResourceKvp("idleCam", "on")
        Notify('Idle cam is enabled!')
    else
        DisableIdleCamera(true)
        SetPedCanPlayAmbientAnims(ped, false)
        SetResourceKvp("idleCam", "off")
        Notify('Idle cam is disabled!')
    end
end)

Citizen.CreateThread(function()
    TriggerEvent("chat:addSuggestion", "/idlecam", "Enable/disable the idle cam")
    local idleCamDisabled = GetResourceKvpString("idleCam") == "off"
    DisableIdleCamera(idleCamDisabled)
end)
  

CreateThread(function()
    local shiftPressed = false
    RegisterKeyMapping('+emote_shortcuts', 'Emote Shortcut Bind', 'keyboard', 'LSHIFT')
    RegisterCommand('+emote_shortcuts', function()
        shiftPressed = true
    end)
    RegisterCommand('-emote_shortcuts', function()
        shiftPressed = false
    end)

    for i = 1, 7 do
        RegisterCommand('emote_shortcuts_' .. i, function(source, args)
            if not shiftPressed then
                return print('Shift is not pressed', i)
            end

            local shortcut = shortcuts[i]
            if shortcut and next(shortcut) and shortcut.id then
                for _, animation in pairs(Config.AllAnimations) do
                    if animation.id == shortcuts[i].id then
                        return onAnimTriggered(animation)
                    end
                end
            end
        end)

        RegisterKeyMapping('emote_shortcuts_' .. i, 'Emote Shortcut ' .. i, 'keyboard',  'NUMPAD'..i)
    end
end)


-- With this event you can do what to do when the emote is canceled.
-- RegisterNetEvent('cylex_animmenuv2:client:onEmoteCancel', function(lastEmote)
--     print(lastEmote)
-- end)

-- With this event you can do what to do when the expression is changed.
-- RegisterNetEvent('cylex_animmenuv2:client:onExpressionSet', function(expression)
--     print(expression)
-- end)

-- With this event you can do what to do when the walk style is changed.
-- RegisterNetEvent('cylex_animmenuv2:client:onWalkSet', function(walkStyle)
--     print(walkStyle)
-- end)

-- You can get the current emote with this export
exports('getCurrentEmote', function()
    return playingEmote
end)

exports('playEmote', function(emote)
    local emote = emote:lower()

    if emote == 'c' or emote == 'cancel' then
        return onEmoteCancel()
    end

    for _, animation in pairs(Config.AllAnimations) do
        if animation.id == emote and animation.category ~= 'walks' and animation.category ~= 'expressions' then
           return onAnimTriggered(animation)
        end
    end

    print('Animation not found: ' .. emote)
    return false
end)


if Config.Ragdoll.enabled then
    RegisterCommand('+ragdoll', function(source, args, raw) Ragdoll() end)
    RegisterCommand('-ragdoll', function(source, args, raw) StopRagdoll() end)
    RegisterKeyMapping("+ragdoll", "Ragdoll your character", "keyboard", Config.Ragdoll.keybind)

    local stop = true
    function Ragdoll()
        if Config.Ragdoll.ragdollAsToggle then
            stop = not stop
        else
            stop = false
        end

        while not stop do
            local ped = PlayerPedId()
            if IsPedOnFoot(ped) then
                SetPedToRagdoll(ped, 1000, 1000, 0, 0, 0, 0)
            end
            Wait(10)
        end
    end

    function StopRagdoll()
        if Config.Ragdoll.AsToggle then return end
        stop = true
    end
end


exports('IsPlayerCrouched', function() return isCrouched end)
exports('IsPlayerProne', function() return IsProne end)
exports('IsPlayerCrawling', function() return isCrawling end)

if Config.Crouch.enabled then
    if Config.Crouch.keybindEnabled then
        RegisterKeyMapping('+crouch', "Crouch", "keyboard", Config.Crouch.keybind)
        RegisterCommand('+crouch', function() CrouchKeyPressed() end, false)
        RegisterCommand('-crouch', function() end, false) -- This needs to be here to prevent errors/warnings
    end
    RegisterCommand('crouch', function()
        if isCrouched then
            isCrouched = false
            return
        end

        AttemptCrouch(PlayerPedId())
    end, false)
    TriggerEvent('chat:addSuggestion', '/crouch', 'Crouch')
end

print(123123)
if Config.Crawl.enabled then
    if Config.Crawl.keybindEnabled then
        RegisterKeyMapping('crawl', "Crawl", "keyboard", Config.Crawl.keybind)
    end
    print(123)
    RegisterCommand('crawl', function() CrawlKeyPressed() end, false)
end