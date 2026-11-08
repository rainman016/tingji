using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("听记")]
[assembly: AssemblyProduct("听记 · 本地视频转文字")]
[assembly: AssemblyVersion("1.0.0.0")]

internal static class DesktopLauncher
{
    private static bool IsProject(string folder)
    {
        return File.Exists(Path.Combine(folder, "app.py")) &&
            File.Exists(Path.Combine(folder, @".venv\Scripts\pythonw.exe"));
    }

    [STAThread]
    private static int Main(string[] args)
    {
        bool checkOnly = args.Length == 1 && args[0] == "--check";
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            if (!IsProject(root))
            {
                using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("Tingji.ProjectRoot"))
                using (StreamReader reader = new StreamReader(stream))
                    root = reader.ReadToEnd().Trim();
            }
            if (!IsProject(root))
                throw new FileNotFoundException("找不到听记程序或 Python 环境，请保留原项目目录，或在新位置重新生成桌面启动器。\n\n项目位置：" + root);
            if (checkOnly) return 0;

            string temporary = Path.Combine(root, ".tmp");
            Directory.CreateDirectory(temporary);
            ProcessStartInfo start = new ProcessStartInfo();
            start.FileName = Path.Combine(root, @".venv\Scripts\pythonw.exe");
            start.Arguments = "\"" + Path.Combine(root, "app.py") + "\"";
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.EnvironmentVariables["TEMP"] = temporary;
            start.EnvironmentVariables["TMP"] = temporary;
            start.EnvironmentVariables["PYTHONUTF8"] = "1";
            start.EnvironmentVariables["HF_HOME"] = Path.Combine(root, @".cache\huggingface");
            start.EnvironmentVariables["MODELSCOPE_CACHE"] = Path.Combine(root, "models");
            using (Process child = Process.Start(start))
            {
                if (child == null) throw new InvalidOperationException("无法启动听记。");
                if (child.WaitForExit(1500) && child.ExitCode != 0)
                    throw new InvalidOperationException("听记启动失败，请检查项目环境，或运行项目中的“启动听记.cmd”查看提示。");
            }
            return 0;
        }
        catch (Exception error)
        {
            if (!checkOnly)
                MessageBox.Show(error.Message, "听记启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
