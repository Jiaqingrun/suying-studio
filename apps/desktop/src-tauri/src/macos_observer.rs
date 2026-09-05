//! Safe bindings for public NSWorkspace notifications.

use block2::RcBlock;
use objc2::rc::Retained;
use objc2::{define_class, msg_send, sel, DefinedClass, MainThreadOnly};
use objc2_app_kit::{
    NSWorkspace, NSWorkspaceDidMountNotification, NSWorkspaceDidUnmountNotification,
    NSWorkspaceDidWakeNotification, NSWorkspaceScreensDidSleepNotification,
    NSWorkspaceScreensDidWakeNotification, NSWorkspaceWillPowerOffNotification,
    NSWorkspaceWillSleepNotification,
};
use objc2_foundation::{
    ns_string, MainThreadMarker, NSDistributedNotificationCenter, NSNotification,
    NSNotificationName, NSObject, NSObjectProtocol, NSOperationQueue,
};
use std::ptr::NonNull;
use tauri::AppHandle;

use super::handle_native_event;
use crate::workspace_events;

fn observe_volume(
    center: &objc2_foundation::NSNotificationCenter,
    queue: &NSOperationQueue,
    name: &'static NSNotificationName,
    app: AppHandle,
    kind: &'static str,
) {
    let callback = RcBlock::new(move |_notification: NonNull<NSNotification>| {
        workspace_events::handle_volume_event(&app, kind);
    });
    let observer = unsafe {
        center.addObserverForName_object_queue_usingBlock(
            Some(name),
            None,
            Some(queue),
            &callback,
        )
    };
    let _ = Retained::into_raw(observer);
}

define_class!(
    #[unsafe(super(NSObject))]
    #[name = "SuyingScreenStateObserver"]
    #[thread_kind = MainThreadOnly]
    #[ivars = AppHandle]
    struct ScreenStateObserver;

    unsafe impl NSObjectProtocol for ScreenStateObserver {}

    impl ScreenStateObserver {
        #[unsafe(method(screenLocked:))]
        fn screen_locked(&self, _notification: &NSNotification) {
            handle_native_event(self.ivars(), "session_inactive");
        }

        #[unsafe(method(screenUnlocked:))]
        fn screen_unlocked(&self, _notification: &NSNotification) {
            handle_native_event(self.ivars(), "session_active");
        }
    }
);

impl ScreenStateObserver {
    fn new(mtm: MainThreadMarker, app: AppHandle) -> Retained<Self> {
        let this = mtm.alloc().set_ivars(app);
        unsafe { msg_send![super(this), init] }
    }
}

fn observe(
    center: &objc2_foundation::NSNotificationCenter,
    queue: &NSOperationQueue,
    name: &'static NSNotificationName,
    app: AppHandle,
    kind: &'static str,
) {
    let callback = RcBlock::new(move |_notification: NonNull<NSNotification>| {
        handle_native_event(&app, kind);
    });
    // SAFETY: `name` is a public AppKit notification constant, `queue` is the
    // main operation queue, and callback has the exact NSNotification block ABI.
    let observer = unsafe {
        center.addObserverForName_object_queue_usingBlock(
            Some(name),
            None,
            Some(queue),
            &callback,
        )
    };
    // Process-lifetime observers: retain the opaque token for as long as Tauri runs.
    let _ = Retained::into_raw(observer);
}

pub fn install(app: AppHandle) {
    let Some(mtm) = MainThreadMarker::new() else {
        return;
    };
    let workspace = NSWorkspace::sharedWorkspace();
    let center = workspace.notificationCenter();
    let queue = NSOperationQueue::mainQueue();

    // SAFETY: these are process-lifetime public AppKit constants.
    unsafe {
        observe(
            &center,
            &queue,
            NSWorkspaceWillSleepNotification,
            app.clone(),
            "will_sleep",
        );
        observe(
            &center,
            &queue,
            NSWorkspaceDidWakeNotification,
            app.clone(),
            "did_wake",
        );
        observe(
            &center,
            &queue,
            NSWorkspaceWillPowerOffNotification,
            app.clone(),
            "will_power_off",
        );
        observe(
            &center,
            &queue,
            NSWorkspaceScreensDidSleepNotification,
            app.clone(),
            "screens_sleep",
        );
        observe(
            &center,
            &queue,
            NSWorkspaceScreensDidWakeNotification,
            app.clone(),
            "screens_wake",
        );
        observe_volume(
            &center,
            &queue,
            NSWorkspaceDidMountNotification,
            app.clone(),
            "did_mount",
        );
        observe_volume(
            &center,
            &queue,
            NSWorkspaceDidUnmountNotification,
            app.clone(),
            "did_unmount",
        );
    }

    // Lock/unlock are distributed notifications; NSWorkspace session-active
    // notifications describe fast-user switching and are not equivalent.
    let screen_observer = ScreenStateObserver::new(mtm, app);
    let distributed = NSDistributedNotificationCenter::defaultCenter();
    unsafe {
        distributed.addObserver_selector_name_object(
            &screen_observer,
            sel!(screenLocked:),
            Some(ns_string!("com.apple.screenIsLocked")),
            None,
        );
        distributed.addObserver_selector_name_object(
            &screen_observer,
            sel!(screenUnlocked:),
            Some(ns_string!("com.apple.screenIsUnlocked")),
            None,
        );
    }
    let _ = Retained::into_raw(screen_observer);
}
