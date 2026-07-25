-- Douyin Safari: location permission → always Allow
tell application "Safari" to activate
delay 0.2
tell application "System Events"
  tell process "Safari"
    set frontmost to true
    delay 0.2
    try
      try
        set cbs to checkboxes of sheet 1 of window 1
        if (count of cbs) > 0 then
          set c to item 1 of cbs
          if value of c is 0 then click c
        end if
      end try
      click button "允许" of sheet 1 of window 1
      return "allow"
    on error
      try
        click button "允许" of window 1
        return "allow"
      on error
        return "none"
      end try
    end try
  end tell
end tell
