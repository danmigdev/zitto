// The program of zitto.app. The first time that a version is opened it installs what zitto needs,
// showing how far it is, and opens itself again. Then it becomes zitto: Python runs inside this
// process, so that for the Mac (Dock, menus, permissions) it is one application.

#import <Cocoa/Cocoa.h>
#include <dlfcn.h>
#include <signal.h>
#include <spawn.h>
#include <sys/file.h>
#include <sys/wait.h>

extern char **environ;

static NSString *InHome(NSString *name) {
    const char *set = getenv("ZITTO_HOME");
    NSString *home = set && *set ? @(set) : [NSHomeDirectory() stringByAppendingPathComponent:
                                             @"Library/Application Support/zitto"];
    return [home stringByAppendingPathComponent:name];
}

static NSString *TextOf(NSString *path) {
    NSString *text = path ? [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:NULL] : nil;
    return [text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
}

static BOOL Installed(void) {
    NSString *wanted = TextOf([NSBundle.mainBundle pathForResource:@"id" ofType:nil]);
    return wanted.length && [wanted isEqualToString:TextOf(InHome(@"installed"))]
        && access(InHome(@"venv/bin/python").fileSystemRepresentation, X_OK) == 0
        && access(InHome(@"libpython.dylib").fileSystemRepresentation, R_OK) == 0;
}

static void SetEnvironment(void) {
    setenv("ZITTO_HOME", InHome(@"").fileSystemRepresentation, 1);
    setenv("ZITTO_OUTPUT", [NSHomeDirectory() stringByAppendingPathComponent:@"Music/zitto"].fileSystemRepresentation, 0);
    const char *path = getenv("PATH");
    NSString *all = [NSString stringWithFormat:@"%@:%s", InHome(@"bin"), path ? path : "/usr/bin:/bin:/usr/sbin:/sbin"];
    setenv("PATH", all.fileSystemRepresentation, 1);
    unsetenv("PYTHONHOME");
    unsetenv("PYTHONPATH");
}

// --- zitto ----------------------------------------------------------------------------------

static int RunZitto(int argc, char **argv) {
    BOOL fromFinder = getppid() == 1;
    if (fromFinder) {  // there is nobody to read what it prints
        freopen(InHome(@"zitto.log").fileSystemRepresentation, "w", stdout);
        dup2(fileno(stdout), fileno(stderr));
    }
    char **args = calloc(argc + 4, sizeof *args);
    int n = 0;
    args[n++] = strdup(InHome(@"venv/bin/python").fileSystemRepresentation);
    args[n++] = "-P";
    args[n++] = "-m";
    args[n++] = "zitto";
    for (int i = 1; i < argc; i++) args[n++] = argv[i];

    int code = 1;
    void *python = dlopen(InHome(@"libpython.dylib").fileSystemRepresentation, RTLD_NOW | RTLD_GLOBAL);
    int (*run)(int, char **) = python ? (int (*)(int, char **))dlsym(python, "Py_BytesMain") : NULL;
    if (run) code = run(n, args);
    else fprintf(stderr, "%s\n", dlerror());
    fflush(NULL);
    if (code && fromFinder) {
        NSString *log = TextOf(InHome(@"zitto.log")) ?: @"";
        if (log.length > 600) log = [log substringFromIndex:log.length - 600];
        NSString *text = [NSString stringWithFormat:@"%@\n\n%@", log, InHome(@"zitto.log")];
        CFUserNotificationDisplayAlert(0, kCFUserNotificationStopAlertLevel, NULL, NULL, NULL,
                                       CFSTR("zitto stopped because of an error"), (__bridge CFStringRef)text,
                                       NULL, NULL, NULL, NULL);
    }
    return code;
}

// --- the first time -------------------------------------------------------------------------

static long long SizeOf(NSString *folder) {
    long long size = 0;
    NSDirectoryEnumerator *files = [NSFileManager.defaultManager
        enumeratorAtURL:[NSURL fileURLWithPath:folder] includingPropertiesForKeys:@[NSURLFileSizeKey] options:0
        errorHandler:^BOOL(NSURL *url, NSError *error) { return YES; }];
    for (NSURL *file in files) {
        NSNumber *one;
        if ([file getResourceValue:&one forKey:NSURLFileSizeKey error:NULL]) size += one.longLongValue;
    }
    return size;
}

@interface Installer : NSObject <NSApplicationDelegate>
@end

@implementation Installer {
    NSWindow *_window;
    NSTextField *_step, *_amount;
    NSProgressIndicator *_bar;
    NSButton *_again;
    NSString *_folder, *_problem;  // what is growing, and what went wrong
    long long _expected;           // how much it will grow
    BOOL _measuring;
    pid_t _child;
    id _awake;
}

- (NSTextField *)label:(NSString *)text at:(NSRect)place size:(CGFloat)size bold:(BOOL)bold dim:(BOOL)dim {
    NSTextField *label = [NSTextField wrappingLabelWithString:text];
    label.frame = place;
    label.selectable = NO;
    label.font = bold ? [NSFont boldSystemFontOfSize:size] : [NSFont systemFontOfSize:size];
    if (dim) label.textColor = NSColor.secondaryLabelColor;
    [_window.contentView addSubview:label];
    return label;
}

- (NSButton *)button:(NSString *)title at:(NSRect)place action:(SEL)action target:(id)target {
    NSButton *button = [NSButton buttonWithTitle:title target:target action:action];
    button.frame = place;
    [_window.contentView addSubview:button];
    return button;
}

- (void)applicationDidFinishLaunching:(NSNotification *)note {
    NSMenuItem *item = [NSMenuItem new];
    item.submenu = [NSMenu new];
    [item.submenu addItemWithTitle:@"Quit zitto" action:@selector(terminate:) keyEquivalent:@"q"];
    NSApp.mainMenu = [NSMenu new];
    [NSApp.mainMenu addItem:item];

    _window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 480, 230)
                                          styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskMiniaturizable
                                            backing:NSBackingStoreBuffered defer:NO];
    _window.title = @"zitto";
    [self label:@"Getting zitto ready" at:NSMakeRect(24, 186, 432, 22) size:15 bold:YES dim:NO];
    [self label:@"Only the first time, it downloads what it needs: about 3.5 GB. Then it opens by itself. "
                 "If you quit, the next time it goes on."
             at:NSMakeRect(24, 144, 432, 36) size:12 bold:NO dim:YES];
    _step = [self label:@"" at:NSMakeRect(24, 94, 432, 34) size:13 bold:NO dim:NO];
    _bar = [[NSProgressIndicator alloc] initWithFrame:NSMakeRect(24, 70, 432, 20)];
    [_window.contentView addSubview:_bar];
    _amount = [self label:@"" at:NSMakeRect(24, 26, 220, 18) size:12 bold:NO dim:YES];
    [self button:@"Quit" at:NSMakeRect(366, 18, 96, 32) action:@selector(terminate:) target:NSApp];
    _again = [self button:@"Try again" at:NSMakeRect(266, 18, 100, 32) action:@selector(start) target:self];
    [_window center];
    [_window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    [NSTimer scheduledTimerWithTimeInterval:1 target:self selector:@selector(measure) userInfo:nil repeats:YES];
    [self start];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)app {
    return YES;
}

- (void)applicationWillTerminate:(NSNotification *)note {
    if (_child > 0) killpg(_child, SIGTERM);
}

- (void)start {
    _again.hidden = YES;
    _problem = nil;
    [self step:@"Starting..." folder:nil expected:0];
    // The Mac must not fall asleep in the middle of a long download.
    _awake = [NSProcessInfo.processInfo beginActivityWithOptions:NSActivityUserInitiated reason:@"Installing"];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        BOOL done = [self install];
        dispatch_async(dispatch_get_main_queue(), ^{ [self ended:done]; });
    });
}

- (void)onScreen:(void (^)(void))what {
    dispatch_async(dispatch_get_main_queue(), what);
}

// Runs install.sh, in the background. The lock is also held by install.sh: it lasts until both end.
- (BOOL)install {
    [NSFileManager.defaultManager createDirectoryAtPath:InHome(@"") withIntermediateDirectories:YES
                                             attributes:nil error:NULL];
    int lock = open(InHome(@"install.lock").fileSystemRepresentation, O_CREAT | O_RDWR, 0644);
    if (lock < 0) {
        _problem = [NSString stringWithFormat:@"Cannot write in %@", InHome(@"")];
        return NO;
    }
    if (flock(lock, LOCK_EX | LOCK_NB)) {
        [self onScreen:^{ [self step:@"Waiting for another copy of zitto, which is doing this..." folder:nil expected:0]; }];
        flock(lock, LOCK_EX);
    }
    int out[2], status = 1;
    if (!Installed() && pipe(out) == 0) {
        posix_spawn_file_actions_t files;
        posix_spawn_file_actions_init(&files);
        posix_spawn_file_actions_adddup2(&files, out[1], STDOUT_FILENO);
        posix_spawn_file_actions_addclose(&files, out[0]);
        posix_spawn_file_actions_addclose(&files, out[1]);
        // In a group, to stop all that it has started; and hearing the signals that this thread does not.
        sigset_t all, none;
        sigfillset(&all);
        sigemptyset(&none);
        posix_spawnattr_t how;
        posix_spawnattr_init(&how);
        posix_spawnattr_setflags(&how, POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_SETSIGDEF | POSIX_SPAWN_SETSIGMASK);
        posix_spawnattr_setpgroup(&how, 0);
        posix_spawnattr_setsigdefault(&how, &all);
        posix_spawnattr_setsigmask(&how, &none);
        NSString *script = [NSBundle.mainBundle pathForResource:@"install" ofType:@"sh"];
        char *args[] = {"/bin/sh", strdup(script.fileSystemRepresentation), NULL};
        pid_t child = 0;
        int failed = posix_spawn(&child, args[0], &files, &how, args, environ);
        close(out[1]);
        FILE *lines = fdopen(out[0], "r");
        if (!failed) {
            _child = child;
            char *line = NULL;
            size_t room = 0;
            while (getline(&line, &room, lines) > 0) [self heard:@(line)];
            free(line);
            waitpid(child, &status, 0);
            _child = 0;
        }
        fclose(lines);
    }
    close(lock);
    return Installed();
}

// A line of install.sh: STEP text folder megabytes, or FAIL text, separated by tabs.
- (void)heard:(NSString *)line {
    NSArray<NSString *> *said = [[line stringByTrimmingCharactersInSet:NSCharacterSet.newlineCharacterSet]
                                 componentsSeparatedByString:@"\t"];
    [self onScreen:^{
        if ([said[0] isEqualToString:@"FAIL"] && said.count > 1) self->_problem = said[1];
        if ([said[0] isEqualToString:@"STEP"] && said.count > 3)
            [self step:said[1] folder:said[2] expected:said[3].longLongValue * 1000000];
    }];
}

- (void)step:(NSString *)text folder:(NSString *)folder expected:(long long)expected {
    _step.stringValue = text;
    _amount.stringValue = @"";
    _folder = folder.length ? folder : nil;
    _expected = expected;
    _bar.hidden = NO;
    _bar.indeterminate = !_folder;
    _bar.doubleValue = 0;
    [_bar startAnimation:nil];
}

- (void)measure {
    NSString *folder = _folder;
    long long expected = _expected;
    if (!folder || !expected || _measuring) return;
    _measuring = YES;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        long long size = MIN(SizeOf(folder), expected);
        dispatch_async(dispatch_get_main_queue(), ^{
            self->_measuring = NO;
            if (self->_folder != folder) return;
            self->_bar.doubleValue = 100.0 * size / expected;
            self->_amount.stringValue = [NSString stringWithFormat:@"%.1f of %.1f GB", size / 1e9, expected / 1e9];
        });
    });
}

- (void)ended:(BOOL)done {
    [NSProcessInfo.processInfo endActivity:_awake];
    if (!done) {
        NSString *why = _problem ?: @"It stopped. Is the internet connection working?";
        [self step:why folder:nil expected:0];
        _bar.hidden = YES;
        _again.hidden = NO;
        return;
    }
    [self step:@"Ready: opening zitto..." folder:nil expected:0];
    // Again, instead of going on here: the window of zitto needs a process that had none.
    NSWorkspaceOpenConfiguration *how = [NSWorkspaceOpenConfiguration configuration];
    how.createsNewApplicationInstance = YES;
    NSArray<NSString *> *all = NSProcessInfo.processInfo.arguments;
    how.arguments = [all subarrayWithRange:NSMakeRange(1, all.count - 1)];
    how.environment = @{@"ZITTO_HOME": @(getenv("ZITTO_HOME")), @"ZITTO_OUTPUT": @(getenv("ZITTO_OUTPUT"))};
    [NSWorkspace.sharedWorkspace openApplicationAtURL:NSBundle.mainBundle.bundleURL configuration:how
                                    completionHandler:^(NSRunningApplication *app, NSError *error) {
        [self onScreen:^{
            if (app && !error) [NSApp terminate:nil];
            else [self step:@"Ready. Quit, and open zitto again." folder:nil expected:0];
        }];
    }];
}

@end

int main(int argc, char **argv) {
    @autoreleasepool {
        SetEnvironment();
        if (Installed()) return RunZitto(argc, argv);
        Installer *installer = [Installer new];
        NSApplication.sharedApplication.delegate = installer;
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
        [NSApp run];
    }
    return 0;
}
