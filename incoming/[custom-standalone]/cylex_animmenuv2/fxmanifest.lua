fx_version 'cerulean'
game 'gta5'
lua54 'yes'
version '1.4'

client_scripts {
    "config.lua",
    "locales/*.lua",
    "animations/*.lua",
    "client/*.lua",
}

server_scripts {
    -- "config.lua",
    "locales/*.lua",
    "server/*.lua"
}

ui_page "html/index.html"

files {
    "animations/AnimationList.json",
    
    "html/index.html",
    "html/assets/*.css",
    -- "html/images/*.png",
    "html/images/*.svg",
    "html/images/no-image.png",
    "html/js/*.js",
    "html/fonts/*.otf",
    "html/fonts/*.ttf",
    "html/fonts/*.TTF",
}


escrow_ignore {
    "config.lua",
    "locales/*.lua",
    "animations/*.lua",
}


dependency '/assetpacks'